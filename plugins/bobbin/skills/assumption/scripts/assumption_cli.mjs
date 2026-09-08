#!/usr/bin/env node
import cli from "../../../dist/cli.js";
process.exitCode = await cli.runCli(["assumption"] .concat(process.argv.slice(2)));
