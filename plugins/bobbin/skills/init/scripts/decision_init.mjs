#!/usr/bin/env node
import cli from "../../../dist/cli.js";
process.exitCode = await cli.runCli(["decision", "init"] .concat(process.argv.slice(2)));
