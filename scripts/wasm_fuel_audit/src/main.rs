//! Test-only audit of *actual staged component bytes*. Not a substring search.
//! wasmparser::Parser::parse_all recursively descends into nested core modules
//! in components, so instruction counts include every embedded function body.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use wasmparser::{Encoding, Operator, Parser, Payload, Validator};

#[derive(Default, Debug)]
struct Counts {
    modules: u64,
    functions: u64,
    call_ref: u64,
    try_table: u64,
}

fn inspect(wasm: &[u8]) -> Result<Counts, Box<dyn Error>> {
    // A malformed binary must never be reported as an innocuous zero-opcode artifact.
    Validator::new().validate_all(wasm)?;
    let mut counts = Counts::default();
    // parse_all enters nested core ModuleSection and ComponentSection payloads.
    for item in Parser::new(0).parse_all(wasm) {
        match item? {
            Payload::Version { encoding: Encoding::Module, .. } => counts.modules += 1,
            Payload::CodeSectionEntry(body) => {
                counts.functions += 1;
                for operator in body.get_operators_reader()? {
                    match operator? {
                        Operator::CallRef { .. } => counts.call_ref += 1,
                        Operator::TryTable { .. } => counts.try_table += 1,
                        _ => {}
                    }
                }
            }
            _ => {}
        }
    }
    if counts.modules == 0 || counts.functions == 0 {
        return Err("no core module/function bodies found: cannot certify instruction inventory".into());
    }
    Ok(counts)
}

fn scan(root: &Path, report: &Path) -> Result<(), Box<dyn Error>> {
    let manifest: Value = serde_json::from_slice(&fs::read(root.join("wasm_provenance.json"))?)?;
    if manifest["schema_version"].as_u64() != Some(1) {
        return Err("missing or unexpected wasm provenance schema".into());
    }
    let pinned = manifest["artifacts"].as_object().ok_or("provenance has no artifacts map")?;
    let mut paths: Vec<PathBuf> = fs::read_dir(root)?
        .filter_map(|entry| entry.ok().map(|x| x.path()))
        .filter(|p| p.extension().is_some_and(|s| s == "wasm"))
        .collect();
    paths.sort();
    if paths.is_empty() || manifest["artifact_count"].as_u64() != Some(paths.len() as u64)
        || pinned.len() != paths.len()
    {
        return Err(format!("provenance inventory incomplete: staged {}, pinned {}",
                           paths.len(), pinned.len()).into());
    }

    let mut records = Vec::new();
    let mut affected = Vec::new();
    let mut modules = 0u64;
    let mut functions = 0u64;
    for path in &paths {
        let name = path.file_name().and_then(|x| x.to_str()).ok_or("invalid Wasm filename")?;
        let bytes = fs::read(path)?;
        let sha = hex::encode(Sha256::digest(&bytes));
        let expected = pinned.get(name).ok_or("staged Wasm missing from provenance")?;
        if expected["sha256"].as_str() != Some(sha.as_str())
            || expected["size_bytes"].as_u64() != Some(bytes.len() as u64)
        {
            return Err(format!("{name}: staged bytes differ from provenance").into());
        }
        let c = inspect(&bytes).map_err(|e| format!("{name}: {e}"))?;
        modules += c.modules;
        functions += c.functions;
        if c.call_ref != 0 || c.try_table != 0 {
            affected.push(name.to_owned());
        }
        records.push(json!({
            "file": name, "sha256": sha, "size_bytes": bytes.len(),
            "nested_core_modules": c.modules, "function_bodies_scanned": c.functions,
            "call_ref": c.call_ref, "try_table": c.try_table
        }));
    }

    let evidence = json!({
        "schema_version": 1, "parser": "wasmparser", "parser_version": "0.255.0",
        "bytecode_inspection": "recursive nested core modules, validated operators",
        "staged_component_count": paths.len(), "nested_core_module_count": modules,
        "function_body_count": functions, "affected_files": affected,
        "components": records,
        "advisory": "GHSA-m63x-6p34-q65x",
        "runtime_under_test": "Wasmtime 47.0.3 (metered Cranelift)"
    });
    if let Some(parent) = report.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(report, serde_json::to_vec_pretty(&evidence)?)?;
    println!("Wasm opcode inventory: {} components, {modules} core modules, {functions} bodies; {} files use affected opcodes; evidence: {}",
             paths.len(), affected.len(), report.display());

    if !affected.is_empty() {
        return Err(format!("metered Wasmtime 47.x fuel-accounting exposure: {} contain call_ref/try_table; see {}",
                           affected.join(", "), report.display()).into());
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = std::env::args_os().skip(1);
    let root = PathBuf::from(args.next().ok_or("usage: wasm-fuel-audit <wasm-dir> <report.json>")?);
    let report = PathBuf::from(args.next().ok_or("missing output report path")?);
    if args.next().is_some() {
        return Err("unexpected extra argument".into());
    }
    scan(&root, &report)
}

#[cfg(test)]
mod tests {
    use super::inspect;

    #[test]
    fn scans_real_function_body_instead_of_matching_text_bytes() {
        // One type, one function and an empty body, encoded as a valid core Wasm module.
        let wasm = [
            0, 97, 115, 109, 1, 0, 0, 0,
            1, 4, 1, 0x60, 0, 0,
            3, 2, 1, 0,
            10, 4, 1, 2, 0, 0x0b,
        ];
        let counts = inspect(&wasm).unwrap();
        assert_eq!((counts.modules, counts.functions), (1, 1));
        assert_eq!((counts.call_ref, counts.try_table), (0, 0));
    }

    #[test]
    fn malformed_wasm_must_not_report_zero_affected_instructions() {
        assert!(inspect(b"not wasm").is_err());
    }
}
