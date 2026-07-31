import test from "node:test";

import { parseTestArgs, runExampleFileTest } from "./example-snapshot.mjs";

const { file: snapshotPath } = parseTestArgs();

test(
  "decompose from example file",
  {
    skip: snapshotPath
      ? false
      : "pass --file to run against a local debug snapshot",
  },
  () => {
    runExampleFileTest();
  },
);
