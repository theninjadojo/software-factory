// The client's version is the factory's: the repository's VERSION file, which the release workflow tags.
fn main() {
    println!("cargo:rerun-if-changed=../VERSION");
    let v = std::fs::read_to_string("../VERSION")
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    let ok = !v.is_empty()
        && v.len() <= 40
        && v.chars()
            .all(|c| c.is_ascii_alphanumeric() || ".-+".contains(c));
    println!(
        "cargo:rustc-env=SHIKUMI_VERSION={}",
        if ok { v.as_str() } else { "dev" }
    );
}
