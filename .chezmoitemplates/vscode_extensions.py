#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ruamel-yaml",
# ]
# ///


import argparse
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import traceback
from typing import Sequence
from ruamel.yaml import YAML
import ruamel.yaml


def get_chezmoi_source_dir():
    # If run by chezmoi, the source path is set in the environment.
    if p := os.getenv("CHEZMOI_SOURCE_DIR"):
        return Path(p)

    # Otherwise, run the chezmoi command to get the source path.
    res = subprocess.run(
        ["chezmoi", "source-path"],
        capture_output=True,
    )
    if res.returncode != 0:
        raise CommandError(res, "Error getting chezmoi source path")
    return Path(res.stdout.decode().strip())


class CommandError(subprocess.SubprocessError):
    """
    Exception for errors in a subprocess command.

    Prints additional info in a more readable format.
    """
    def __init__(self, cmd: subprocess.CompletedProcess, msg: str = None):
        self.msg = msg or "Command failed"
        self.cmd = cmd

    def __str__(self):
        msg = f"{self.msg}\n\nCommand: {self.cmd.args}\n\nReturn code: {self.cmd.returncode}"

        for attr in "stdout", "stderr":
            try:
                val = getattr(self.cmd, attr).decode().rstrip()
            except AttributeError:
                continue
            msg += f"\n\n{attr.capitalize()}:\n{textwrap.indent(val, ' ' * 4)}"
        return msg


class VSCodeExtensions:
    source_file = get_chezmoi_source_dir() / ".chezmoidata/vscode_extensions.yaml"
    source_key = "vscode_extensions"
    _installed = None
    _source = None

    def __init__(self):
        # "Round Trip" type load/dump, to preserve comments
        yaml = YAML(typ='rt')
        yaml.indent(mapping=2, sequence=4, offset=2)
        self.yaml = yaml

    @property
    def installed(self) -> list[str]:
        if self._installed is None:
            res = subprocess.run(["code", "--list-extensions"], capture_output=True)
            if res.stderr:
                raise CommandError(res, "Error getting installed extensions")
            self._installed = res.stdout.decode().splitlines()
        return self._installed

    @property
    def source(self) -> ruamel.yaml.comments.CommentedMap:
        if self._source is None:
            with self.source_file.open() as f:
                self._source = self.yaml.load(f)
        return self._source

    def write_source(self) -> None:
        with self.source_file.open("w") as f:
            self.yaml.dump(self.source, f)

    def add(self, *extensions: str) -> None:
        """
        Add extensions to the source state.

        Duplicates are always removed, and the extension list is sorted before
        it is written.
        """
        # Remove duplicates
        extensions = set(extensions) - set(self.source[self.source_key])

        self.source[self.source_key] += extensions
        self.source[self.source_key].sort()
        self.write_source()

    def install(self, *extensions: str) -> list[str]:
        """
        Install extensions in VS Code.

        Must install extensions one command at a time, since an error installing
        any extension causes the command to exit without attempting to install
        others.
        """
        # Default to installing all extensions in the source state
        if not extensions:
            extensions = self.source[self.source_key]

        # Remove extensions that are already installed
        extensions = set(extensions) - set(self.installed)

        failed_to_install: list[str] = []

        for ext in sorted(extensions):
            res = subprocess.run(["code", "--install-extension", ext], capture_output=True)
            if res.returncode != 0 or res.stderr or not any(x in res.stdout.decode() for x in ("already installed", "successfully installed")):
                traceback.print_exception(CommandError(res, f"Failed to install extension '{ext}'"))
                print("\n", file=sys.stderr)

                failed_to_install.append(ext)

        return failed_to_install

    def diff(self) -> None:
        """
        Compare installed extensions with the source state.
        """
        source = set(self.source[self.source_key])
        installed = set(self.installed)

        for exts, msg in (
            (sorted(installed - source), "Installed locally but not in source state"),
            (sorted(source - installed), "In source state but not installed locally"),
        ):
            print(f"{msg}:")
            for ext in exts:
                print(" " * 4 + ext)
            print()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage VS Code extensions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add extensions to the source state")
    add.add_argument("extensions", nargs="+", help="Extensions to add")

    install = subparsers.add_parser("install", help="Install extensions in VS Code")
    install.add_argument("extensions", nargs="*", help="Extensions to install. If none are given, all extensions from the source state will be installed.")

    diff = subparsers.add_parser("diff", help="Compare installed extensions with the source state")  # noqa: F841

    args = parser.parse_args(argv)

    extensions = VSCodeExtensions()

    match args.command:
        case "add":
            extensions.add(*args.extensions)

        case "install":
            failed = extensions.install(*args.extensions)
            if failed:
                msg = "The following extensions failed to install:"
                for ext in failed:
                    msg += "\n" + " " * 4 + ext
                raise RuntimeError(msg)

        case "diff":
            extensions.diff()


if __name__ == "__main__":
    if os.getenv("CHEZMOI") == "1":
        # If run by chezmoi, just install extensions
        main(["install"])
    else:
        main()
