//! The factories this client knows: `~/.config/shikumi/cli.toml` (or `$SHIKUMI_CONFIG`), one `[instances.<name>]` table each.
//! The file holds tokens, so it is written with mode 0600 in a 0700 folder, and a token is never read from a file others can read
//! (as ssh does with keys).

use anyhow::{anyhow, bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct Config {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default: Option<String>,
    #[serde(default)]
    pub instances: BTreeMap<String, Instance>,
}

#[derive(Debug, Default, Clone, Serialize, Deserialize, PartialEq)]
pub struct Instance {
    pub url: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_file: Option<PathBuf>,
    /// The repository a short ref (`#12`, `L-3`) means on this factory.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repo: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub allow_insecure_http: bool,
}

/// What a command talks to: a factory's address and token, picked from the file or the environment.
#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub name: String,
    pub url: String,
    pub token: String,
    pub repo: Option<String>,
    pub allow_insecure_http: bool,
}

/// The environment variables that choose a factory, passed in so tests do not touch the real environment.
#[derive(Debug, Default, Clone)]
pub struct Env {
    pub instance: Option<String>,
    pub url: Option<String>,
    pub token: Option<String>,
    pub config: Option<PathBuf>,
    pub home: Option<PathBuf>,
    pub xdg_config: Option<PathBuf>,
}

impl Env {
    pub fn from_process() -> Env {
        let get = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        Env {
            instance: get("SHIKUMI_INSTANCE"),
            url: get("SHIKUMI_URL"),
            token: get("SHIKUMI_TOKEN"),
            config: get("SHIKUMI_CONFIG").map(PathBuf::from),
            home: get("HOME").map(PathBuf::from),
            xdg_config: get("XDG_CONFIG_HOME").map(PathBuf::from),
        }
    }

    pub fn config_path(&self) -> Result<PathBuf> {
        if let Some(p) = &self.config {
            return Ok(p.clone());
        }
        let base = match (&self.xdg_config, &self.home) {
            (Some(x), _) => x.clone(),
            (None, Some(h)) => h.join(".config"),
            _ => {
                bail!("set HOME or SHIKUMI_CONFIG: there is nowhere to keep the list of factories")
            }
        };
        Ok(base.join("shikumi").join("cli.toml"))
    }
}

pub fn valid_name(name: &str) -> bool {
    let b = name.as_bytes();
    !b.is_empty()
        && b.len() <= 40
        && b[0].is_ascii_alphanumeric()
        && b.iter()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
}

impl Config {
    pub fn load(path: &Path) -> Result<Config> {
        let text = match fs::read_to_string(path) {
            Ok(t) => t,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Config::default()),
            Err(e) => return Err(e).with_context(|| format!("cannot read {}", path.display())),
        };
        let cfg: Config =
            toml::from_str(&text).with_context(|| format!("{} is not valid", path.display()))?;
        if cfg.instances.values().any(|i| i.token.is_some()) {
            private(path)?;
        }
        Ok(cfg)
    }

    /// Written to a temporary file in the same folder (0600) and moved into place, so a crash never leaves half a file.
    pub fn save(&self, path: &Path) -> Result<()> {
        let dir = path
            .parent()
            .ok_or_else(|| anyhow!("{} has no folder", path.display()))?;
        fs::create_dir_all(dir).with_context(|| format!("cannot create {}", dir.display()))?;
        let _ = fs::set_permissions(dir, fs::Permissions::from_mode(0o700));
        let tmp = dir.join(format!(".cli.toml.{}", std::process::id()));
        let text = toml::to_string_pretty(self)?;
        {
            let mut f = fs::OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .mode(0o600)
                .open(&tmp)
                .with_context(|| format!("cannot write {}", tmp.display()))?;
            f.write_all(text.as_bytes())?;
            f.sync_all()?;
        }
        fs::rename(&tmp, path).with_context(|| format!("cannot write {}", path.display()))?;
        Ok(())
    }

    /// The factory a command talks to: `--instance`, then SHIKUMI_INSTANCE, then SHIKUMI_URL with SHIKUMI_TOKEN, then the
    /// default, then the only one there is. SHIKUMI_TOKEN alone replaces the chosen instance's token.
    pub fn resolve(&self, flag: Option<&str>, env: &Env) -> Result<Target> {
        let named = flag.map(str::to_string).or_else(|| env.instance.clone());
        if named.is_none() {
            if let Some(url) = &env.url {
                let token = env
                    .token
                    .clone()
                    .ok_or_else(|| anyhow!("SHIKUMI_URL is set: set SHIKUMI_TOKEN too"))?;
                return Ok(Target {
                    name: "(SHIKUMI_URL)".into(),
                    url: url.clone(),
                    token,
                    repo: None,
                    allow_insecure_http: false,
                });
            }
        }
        let name = match named.or_else(|| self.default.clone()) {
            Some(n) => n,
            None if self.instances.len() == 1 => self.instances.keys().next().cloned().unwrap_or_default(),
            None if self.instances.is_empty() => bail!("no factory is set up yet: run `shikumi instance add <name> --url <address>`"),
            None => bail!("several factories are set up: choose one with --instance, or set a default with `shikumi instance default <name>`"),
        };
        let inst = self.instances.get(&name).ok_or_else(|| {
            anyhow!("there is no factory called {name} (see `shikumi instance list`)")
        })?;
        let token = match (&env.token, &inst.token, &inst.token_file) {
            (Some(t), _, _) => t.clone(),
            (None, Some(t), _) => t.clone(),
            (None, None, Some(f)) => {
                private(f)?;
                fs::read_to_string(f).with_context(|| format!("cannot read the token file {}", f.display()))?.trim().to_string()
            }
            (None, None, None) => bail!("the factory {name} has no token: add it again with `shikumi instance add {name} --url ...`"),
        };
        Ok(Target {
            name,
            url: inst.url.clone(),
            token,
            repo: inst.repo.clone(),
            allow_insecure_http: inst.allow_insecure_http,
        })
    }
}

/// Refuse a file holding a token that group or others can read.
fn private(path: &Path) -> Result<()> {
    let meta = fs::metadata(path).with_context(|| format!("cannot read {}", path.display()))?;
    if meta.permissions().mode() & 0o077 != 0 {
        bail!(
            "{} holds a token and others can read it. Run: chmod 600 {}",
            path.display(),
            path.display()
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "shikumi-cfg-{}-{}",
            std::process::id(),
            rand_suffix()
        ));
        fs::create_dir_all(&d).unwrap();
        d
    }

    fn rand_suffix() -> u128 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    }

    fn two() -> Config {
        let mut c = Config::default();
        c.instances.insert(
            "home".into(),
            Instance {
                url: "http://127.0.0.1:8787".into(),
                token: Some("shk_h".into()),
                ..Default::default()
            },
        );
        c.instances.insert(
            "work".into(),
            Instance {
                url: "https://f.example".into(),
                token: Some("shk_w".into()),
                repo: Some("o/r".into()),
                ..Default::default()
            },
        );
        c
    }

    #[test]
    fn which_factory_a_command_uses() {
        let mut c = two();
        let env = Env::default();
        assert!(c.resolve(None, &env).is_err()); // two, no default
        assert_eq!(c.resolve(Some("work"), &env).unwrap().token, "shk_w");
        c.default = Some("home".into());
        assert_eq!(c.resolve(None, &env).unwrap().name, "home");
        let e = Env {
            instance: Some("work".into()),
            ..Default::default()
        };
        assert_eq!(c.resolve(None, &e).unwrap().name, "work");
        assert_eq!(c.resolve(Some("home"), &e).unwrap().name, "home"); // the flag wins
        let e = Env {
            url: Some("http://127.0.0.1:9".into()),
            token: Some("shk_e".into()),
            ..Default::default()
        };
        let t = c.resolve(None, &e).unwrap();
        assert_eq!(
            (t.url.as_str(), t.token.as_str()),
            ("http://127.0.0.1:9", "shk_e")
        );
        let e = Env {
            token: Some("shk_override".into()),
            ..Default::default()
        };
        assert_eq!(c.resolve(Some("work"), &e).unwrap().token, "shk_override");
        assert!(c.resolve(Some("nope"), &env).is_err());
        let e = Env {
            url: Some("http://x".into()),
            ..Default::default()
        };
        assert!(c.resolve(None, &e).is_err()); // a URL without a token
        let mut one = Config::default();
        one.instances
            .insert("solo".into(), two().instances["home"].clone());
        assert_eq!(one.resolve(None, &env).unwrap().name, "solo");
        assert!(Config::default().resolve(None, &env).is_err());
    }

    #[test]
    fn saved_privately_and_read_back() {
        let d = tmp();
        let p = d.join("sub").join("cli.toml");
        two().save(&p).unwrap();
        assert_eq!(
            fs::metadata(&p).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(p.parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(Config::load(&p).unwrap(), two());
        fs::set_permissions(&p, fs::Permissions::from_mode(0o644)).unwrap();
        let err = Config::load(&p).unwrap_err().to_string();
        assert!(err.contains("chmod 600"), "{err}");
        assert_eq!(
            Config::load(&d.join("missing.toml")).unwrap(),
            Config::default()
        );
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn a_token_file_must_be_private_too() {
        let d = tmp();
        let f = d.join("token");
        fs::write(&f, "shk_file\n").unwrap();
        fs::set_permissions(&f, fs::Permissions::from_mode(0o600)).unwrap();
        let mut c = Config::default();
        c.instances.insert(
            "x".into(),
            Instance {
                url: "http://127.0.0.1:1".into(),
                token_file: Some(f.clone()),
                ..Default::default()
            },
        );
        assert_eq!(c.resolve(None, &Env::default()).unwrap().token, "shk_file");
        fs::set_permissions(&f, fs::Permissions::from_mode(0o640)).unwrap();
        assert!(c.resolve(None, &Env::default()).is_err());
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn where_the_file_lives() {
        let e = Env {
            home: Some("/h".into()),
            ..Default::default()
        };
        assert_eq!(
            e.config_path().unwrap(),
            PathBuf::from("/h/.config/shikumi/cli.toml")
        );
        let e = Env {
            home: Some("/h".into()),
            xdg_config: Some("/x".into()),
            ..Default::default()
        };
        assert_eq!(
            e.config_path().unwrap(),
            PathBuf::from("/x/shikumi/cli.toml")
        );
        let e = Env {
            config: Some("/c.toml".into()),
            ..Default::default()
        };
        assert_eq!(e.config_path().unwrap(), PathBuf::from("/c.toml"));
        assert!(Env::default().config_path().is_err());
    }

    #[test]
    fn names() {
        for ok in ["home", "work-2", "a"] {
            assert!(valid_name(ok), "{ok}");
        }
        for bad in ["", "-x", "Home", "a b", "a/b", &"x".repeat(41)] {
            assert!(!valid_name(bad), "{bad}");
        }
    }
}
