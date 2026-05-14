from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from cocoindex_code.cli import app as ccc_app

# Create a new Typer application to wrap the original one
app = typer.Typer(
    help="Wrapper for cocoindex-code with global --root support.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback()
def main(
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            "-r",
            help="Specify the project root directory. MUST be placed before the command (e.g. cs -r path search).",
            exists=True,
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """
    CocoIndex Code Wrapper.
    """
    if root:
        os.chdir(root)

    # Manually run the original ccc_app callback logic (Honor COCOINDEX_CODE_HOST_CWD)
    # to maintain full compatibility since typer ignores the sub-app callback in name="" mode.
    host_cwd = os.environ.get("COCOINDEX_CODE_HOST_CWD")
    if host_cwd:
        try:
            from cocoindex_code.settings import normalize_input_path

            target = normalize_input_path(host_cwd)
            os.chdir(target)
        except (ImportError, OSError):
            pass


# To silence the UserWarning about ignored callbacks when using add_typer(name=""),
# we explicitly nullify the sub-app's callback before adding it.
ccc_app.registered_callback = None
app.add_typer(ccc_app, name="")

if __name__ == "__main__":
    app()
