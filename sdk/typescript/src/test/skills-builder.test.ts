import assert from "node:assert/strict";
import { mkdtemp, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { SkillsBuilder } from "../pageindex.js";

async function createSkillDir(): Promise<string> {
  const tmp = await mkdtemp(join(tmpdir(), "cyt-skills-"));
  const skillsDir = join(tmp, "skills");
  await mkdir(skillsDir, { recursive: true });
  await writeFile(join(skillsDir, "x.md"), "# X\n\nY", "utf8");
  return skillsDir;
}

test("SkillsBuilder buildFromFile indexes a single skill markdown file", async () => {
  const skillsDir = await createSkillDir();
  const builder = new SkillsBuilder({ memoryOnly: true });
  const index = builder.buildFromFile(join(skillsDir, "x.md"));
  assert.ok(Object.keys(index.files ?? {}).length > 0);
});

test("SkillsBuilder buildFromDirs indexes skill markdown", async () => {
  const skillsDir = await createSkillDir();
  const builder = new SkillsBuilder({ memoryOnly: true });
  const index = builder.buildFromDirs([skillsDir]);
  assert.ok(Object.keys(index.files ?? {}).length > 0);
});

test("SkillsBuilder writeCatalog returns indexed files", async () => {
  const skillsDir = await createSkillDir();
  const builder = new SkillsBuilder({ memoryOnly: true });
  builder.buildFromDirs([skillsDir]);
  const catalog = builder.writeCatalog();
  assert.ok(Object.keys(catalog.files ?? {}).length > 0);
});

test("SkillsBuilder serializes skills index and dict", async () => {
  const skillsDir = await createSkillDir();
  const builder = new SkillsBuilder({ memoryOnly: true });
  builder.buildFromDirs([skillsDir]);
  assert.ok(typeof builder.toSkillsIndexJson() === "object");
  assert.ok(typeof builder.toSkillsDict() === "object");
});
