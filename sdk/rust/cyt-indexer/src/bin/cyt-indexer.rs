use clap::{Parser, Subcommand};
use cyt_indexer::{
    build_catalog_index, load_catalog_from_dir, retrieve_core, DecomposedCatalog,
    ProcessGroupsOptions, RetrieveOptions,
};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "cyt-indexer")]
#[command(about = "Tool schema decomposition and catalog indexing")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Build a decomposed catalog index from tools JSON
    Build {
        #[arg(long)]
        tools: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
    /// Retrieve merged tool schemas from pruner output
    Retrieve {
        #[arg(long)]
        catalog: PathBuf,
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
    },
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Build { tools, output } => {
            let raw = fs::read_to_string(tools)?;
            let tools_val: Value = serde_json::from_str(&raw)?;
            let tools_arr = tools_val
                .as_array()
                .cloned()
                .or_else(|| tools_val.get("tools").and_then(|v| v.as_array()).cloned())
                .ok_or("Expected tools array in input JSON")?;
            let index = build_catalog_index(&tools_arr, &[]);
            fs::create_dir_all(&output)?;
            for (rel, content) in &index.files {
                let path = output.join(rel);
                if let Some(parent) = path.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::write(path, content)?;
            }
            eprintln!("Wrote {} files to {}", index.files.len(), output.display());
        }
        Commands::Retrieve {
            catalog,
            input,
            output,
        } => {
            let catalog_dict = load_catalog_from_dir(catalog.to_str().unwrap())?;
            let mut store = DecomposedCatalog::from_catalog_dict(&catalog_dict);
            let input_raw = fs::read_to_string(input)?;
            let data: Value = serde_json::from_str(&input_raw)?;
            let survivor = DecomposedCatalog::from_catalog_dict(&data);
            let opts = RetrieveOptions {
                apply_decomposed_score_filter: true,
                process_groups: ProcessGroupsOptions::default(),
            };
            let tools = retrieve_core(&data, &mut store, &survivor, &opts);
            fs::write(output, serde_json::to_string_pretty(&tools)?)?;
        }
    }
    Ok(())
}
