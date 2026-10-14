//! Talking to a factory's JSON API (/api/v1/). The token goes in an Authorization header on every request; redirects are never
//! followed, so it is never sent to another address (and a factory too old to have the API, which answers with a redirect to its
//! login page, is recognised). Plain http is refused for anything but this machine unless the person allows it.

use anyhow::{bail, Result};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fmt;
use std::net::IpAddr;
use std::time::Duration;

pub const VERSION: &str = env!("SHIKUMI_VERSION");
pub const API: u64 = 1;

/// A factory address made safe to use: `scheme://host[:port][/path]` without a trailing slash, and whether it is plain http
/// to another machine (allowed only when asked for, with a warning).
#[derive(Debug, Clone, PartialEq)]
pub struct Base {
    pub url: String,
    pub insecure: bool,
}

pub fn check_url(url: &str, allow_insecure_http: bool) -> Result<Base> {
    let url = url.trim();
    let (scheme, rest) = url.split_once("://").ok_or_else(|| {
        anyhow::anyhow!("{url:?} is not an address: write it as http://host:port or https://host")
    })?;
    let scheme = scheme.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        bail!("{url:?}: only http and https addresses are used");
    }
    if rest.contains(['?', '#']) || rest.chars().any(|c| c.is_whitespace() || c.is_control()) {
        bail!("{url:?}: give the factory's address only, without a query or fragment");
    }
    let (authority, path) = rest
        .split_once('/')
        .map(|(a, p)| (a, format!("/{p}")))
        .unwrap_or((rest, String::new()));
    if authority.is_empty() || authority.contains('@') {
        bail!("{url:?}: the address must name a host, and must not carry a user or password");
    }
    let host = if let Some(h) = authority.strip_prefix('[') {
        h.split(']').next().unwrap_or("").to_string()
    } else {
        authority
            .rsplit_once(':')
            .map(|(h, _)| h)
            .unwrap_or(authority)
            .to_string()
    };
    if host.is_empty() {
        bail!("{url:?}: the address must name a host");
    }
    let local = host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .map(|ip| ip.is_loopback())
            .unwrap_or(false);
    let insecure = scheme == "http" && !local;
    if insecure && !allow_insecure_http {
        bail!(
            "{url}: plain http to another machine would send the token unencrypted. Use https, an SSH tunnel \
             (ssh -N -L 8787:127.0.0.1:8787 <host>, then http://127.0.0.1:8787), or --allow-insecure-http on a network you trust"
        );
    }
    Ok(Base {
        url: format!("{scheme}://{authority}{}", path.trim_end_matches('/')),
        insecure,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub enum ApiError {
    /// The factory answered with an error of its own.
    Api {
        status: u16,
        code: String,
        message: String,
        fields: BTreeMap<String, String>,
    },
    /// The address answered with a redirect: a factory without the API, or not a factory.
    NoApi(String),
    /// Nothing usable came back: the connection failed or the reply was not the API's.
    Transport(String),
}

impl fmt::Display for ApiError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            ApiError::Api {
                message, fields, ..
            } => {
                write!(f, "{message}")?;
                for (k, v) in fields {
                    write!(f, "\n  {k}: {v}")?;
                }
                Ok(())
            }
            ApiError::NoApi(m) | ApiError::Transport(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for ApiError {}

impl ApiError {
    /// The exit code for this error (see main.rs).
    pub fn exit_code(&self) -> i32 {
        match self {
            ApiError::Api {
                status: 401 | 403, ..
            } => 3,
            ApiError::Api { status: 404, .. } => 4,
            ApiError::Api {
                status: 409 | 422 | 400,
                ..
            } => 5,
            _ => 6,
        }
    }
}

pub struct Client {
    base: String,
    token: String,
    agent: ureq::Agent,
}

impl Client {
    pub fn new(base: &Base, token: &str) -> Client {
        let config = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .max_redirects(0)
            .max_redirects_will_error(false)
            .timeout_connect(Some(Duration::from_secs(10)))
            .timeout_global(Some(Duration::from_secs(90)))
            .user_agent(format!("shikumi-cli/{VERSION}"))
            .build();
        Client {
            base: base.url.clone(),
            token: token.trim().to_string(),
            agent: config.into(),
        }
    }

    fn url(&self, path: &str, query: &[(&str, String)]) -> String {
        let mut u = format!("{}/api/v1/{}", self.base, path);
        let q: Vec<String> = query
            .iter()
            .filter(|(_, v)| !v.is_empty())
            .map(|(k, v)| format!("{k}={}", encode(v)))
            .collect();
        if !q.is_empty() {
            u.push('?');
            u.push_str(&q.join("&"));
        }
        u
    }

    pub fn get<T: DeserializeOwned>(
        &self,
        path: &str,
        query: &[(&str, String)],
    ) -> Result<T, ApiError> {
        let req = self
            .agent
            .get(&self.url(path, query))
            .header("Authorization", &format!("Bearer {}", self.token));
        Self::finish(req.call())
    }

    pub fn post<T: DeserializeOwned>(&self, path: &str, body: &Value) -> Result<T, ApiError> {
        let req = self
            .agent
            .post(&self.url(path, &[]))
            .header("Authorization", &format!("Bearer {}", self.token))
            .content_type("application/json");
        Self::finish(req.send(body.to_string()))
    }

    fn finish<T: DeserializeOwned>(
        got: Result<ureq::http::Response<ureq::Body>, ureq::Error>,
    ) -> Result<T, ApiError> {
        let mut resp =
            got.map_err(|e| ApiError::Transport(format!("could not reach the factory: {e}")))?;
        let status = resp.status().as_u16();
        if (300..400).contains(&status) {
            return Err(ApiError::NoApi(
                "the address answered with a redirect, not the API: the factory is older than its command-line API (update it), or the address is not a factory's".into(),
            ));
        }
        let text = resp
            .body_mut()
            .with_config()
            .limit(64 * 1024 * 1024)
            .read_to_string()
            .map_err(|e| {
                ApiError::Transport(format!("the factory's reply could not be read: {e}"))
            })?;
        let v: Value = serde_json::from_str(&text).map_err(|_| {
            ApiError::Transport(format!("the address answered {status} without the API's JSON: is it a factory, and is the address right?"))
        })?;
        if !(200..300).contains(&status) {
            let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            let fields = v
                .get("fields")
                .and_then(Value::as_object)
                .map(|m| {
                    m.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
                        .collect()
                })
                .unwrap_or_default();
            let message = if s("error").is_empty() {
                format!("the factory answered {status}")
            } else {
                s("error")
            };
            return Err(ApiError::Api {
                status,
                code: s("code"),
                message,
                fields,
            });
        }
        serde_json::from_value(v).map_err(|e| {
            ApiError::Transport(format!(
                "the factory's reply was not what this client expects ({e}); update the client"
            ))
        })
    }
}

/// Percent-encoding for a query value (everything but unreserved characters).
pub fn encode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn addresses() {
        let ok = |u: &str| check_url(u, false).unwrap().url;
        assert_eq!(ok("http://127.0.0.1:8787/"), "http://127.0.0.1:8787");
        assert_eq!(ok("http://localhost:8787"), "http://localhost:8787");
        assert_eq!(ok("http://[::1]:8787"), "http://[::1]:8787");
        assert_eq!(ok("http://127.8.9.10"), "http://127.8.9.10");
        assert_eq!(
            ok("https://factory.example/shikumi/"),
            "https://factory.example/shikumi"
        );
        assert_eq!(ok("HTTPS://f.example"), "https://f.example");
        for bad in [
            "http://192.168.1.138:8787",
            "http://factory.example",
            "http://[fe80::1]:8787",
            "http://localhost.evil.example",
        ] {
            assert!(check_url(bad, false).is_err(), "{bad}");
            assert!(check_url(bad, true).unwrap().insecure, "{bad}");
        }
        for bad in [
            "ftp://x",
            "f.example",
            "https://user:pw@f.example",
            "https://f.example/?x=1",
            "https://f.example/#a",
            "https://",
            "https://:80",
            "https://f.ex ample",
        ] {
            assert!(check_url(bad, true).is_err(), "{bad}");
        }
    }

    #[test]
    fn exit_codes() {
        let api = |status| ApiError::Api {
            status,
            code: String::new(),
            message: String::new(),
            fields: BTreeMap::new(),
        };
        assert_eq!(
            [401, 403, 404, 409, 422, 429, 502, 503].map(|s| api(s).exit_code()),
            [3, 3, 4, 5, 5, 6, 6, 6]
        );
        assert_eq!(ApiError::NoApi(String::new()).exit_code(), 6);
    }

    #[test]
    fn query_values_are_encoded() {
        assert_eq!(encode("a b&c=d/é"), "a%20b%26c%3Dd%2F%C3%A9");
    }
}
