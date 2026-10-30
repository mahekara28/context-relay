import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RelayStore } from "../../../dist/store.js";
import { proposalPreview, generateProposal } from "../../../dist/proposals.js";
import { markdownPacket, launchPlan } from "../../../dist/delivery.js";
test("Nebius sends only selected redacted evidence, validates citations, and never writes a checkpoint", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "relay-proposal-"));
  mkdirSync(join(root, "project"));
  const store = new RelayStore(join(root, "data"));
  const project = store.addProject({ root: join(root, "project") });
  const session = store.importSession({
    projectId: project.id,
    harness: "claude",
    externalId: "test",
    title: "Test",
    events: [
      { id: "one", role: "user", text: "Use 7 days. api_key=fixture-value" },
      { id: "two", role: "user", text: "Unselected sensitive event" },
    ],
  }).session;
  const oldKey = process.env.NEBIUS_API_KEY,
    oldModel = process.env.NEBIUS_MODEL;
  process.env.NEBIUS_API_KEY = "synthetic-test-key";
  process.env.NEBIUS_MODEL = "fixture-model";
  t.after(() => {
    if (oldKey === undefined) delete process.env.NEBIUS_API_KEY;
    else process.env.NEBIUS_API_KEY = oldKey;
    if (oldModel === undefined) delete process.env.NEBIUS_MODEL;
    else process.env.NEBIUS_MODEL = oldModel;
    store.close();
    rmSync(root, { recursive: true, force: true });
  });
  const ref = { sessionId: session.id, eventId: "one" };
  const input = { projectId: project.id, evidence: [ref] };
  const preview = proposalPreview(store, input);
  assert.equal(preview.events.length, 1);
  assert.match(preview.events[0].text, /REDACTED/);
  const proposal = {
    title: "Continue",
    goal: "Handle duplicate delivery",
    instructions: [{ key: "retention", text: "Use 7 days.", sources: [ref] }],
    criteria: ["One charge per event"],
    openWork: ["Implement retries"],
  };
  const transport = async (url, options) => {
    assert.equal(
      url,
      "https://api.tokenfactory.nebius.com/v1/chat/completions",
    );
    const body = JSON.parse(options.body);
    assert(!body.messages[1].content.includes("Unselected"));
    assert(!body.messages[1].content.includes("fixture-value"));
    return new Response(
      JSON.stringify({
        choices: [
          {
            finish_reason: "stop",
            message: { content: JSON.stringify(proposal) },
          },
        ],
      }),
    );
  };
  await assert.rejects(() => generateProposal(store, input, transport));
  const result = await generateProposal(
    store,
    { ...input, consent: true },
    transport,
  );
  assert.equal(result.requiresReview, true);
  assert.equal(store.checkpoints().length, 0);
  proposal.instructions[0].sources = [
    { sessionId: session.id, eventId: "two" },
  ];
  await assert.rejects(
    () => generateProposal(store, { ...input, consent: true }, transport),
    { code: "invalid_proposal" },
  );
  await assert.rejects(
    () =>
      generateProposal(
        store,
        { ...input, consent: true },
        async () => new Response("{}", { status: 429 }),
      ),
    { code: "provider_error" },
  );
  await assert.rejects(
    () =>
      generateProposal(
        store,
        { ...input, consent: true },
        async () =>
          new Response(
            JSON.stringify({ choices: [{ message: { content: "not json" } }] }),
          ),
      ),
    { code: "invalid_proposal" },
  );
  const c = store.createCheckpoint({
    projectId: project.id,
    title: "Reviewed",
    goal: "Continue",
    sources: [{ sessionId: session.id, revision: 1 }],
    evidence: [ref],
  });
  const h = store.offer({ checkpointId: c.id, recipient: "codex" });
  const md = markdownPacket(h);
  assert.match(md, /untrusted quotations/);
  assert.match(md, /> Use 7 days/);
  assert.equal(store.handoff(h.id).status, "offered");
});

test("interactive launch preserves native approvals and keeps the prompt in one argument", () => {
  const h = { id: "synthetic-id", recipient: "codex", status: "offered" };
  const plan = launchPlan(h, "/project with spaces");
  assert.equal(plan.command, "codex");
  assert.equal(plan.args.length, 1);
  assert.match(plan.args[0], /synthetic-id/);
  assert(!plan.args.some((arg) => arg.startsWith("--")));
  assert.throws(() => launchPlan({ ...h, status: "accepted" }, "/project"));
  assert.throws(() => launchPlan({ ...h, recipient: "goose" }, "/project"));
});
