// isolated-workflow template.
// A fresh agent implements; separate lenses verify cold; a fix agent repairs real issues only.
// Customize REPO, the tiers, AUTHORIZATION, IMPLEMENT_TASK, and the verify prompts, then pass
// this to the workflow runtime (written for Claude Code's Workflow tool). The main loop owns
// scope, branch, commit, and any stateless oracle red-team pass, OUTSIDE this script.

export const meta = {
  name: 'isolated-workflow',
  description: 'Fresh implementer + separate reviewers for a scoped build',
  phases: [
    { title: 'Implement', detail: 'A fresh-context agent writes the code' },
    { title: 'Verify', detail: 'Separate lenses: functional + editorial' },
    { title: 'Fix', detail: 'A fresh agent fixes real issues only' },
  ],
};

const REPO = '/ABSOLUTE/PATH/TO/REPO'; // <-- set me

// Tier aliases your runtime accepts. Judgment tier for judgment-heavy code; switch the build to
// the fast tier at 'medium' effort for mechanical edits.
const JUDGMENT_MODEL = 'opus';
const FAST_MODEL = 'sonnet';
const BUILD_MODEL = JUDGMENT_MODEL;
const BUILD_EFFORT = 'high';

// The human approval this work rests on, quoted rather than summarized. See
// docs/concepts/authorization-provenance.md. Fill every field from observed instructions.
const AUTHORIZATION = [
  'AUTHORIZATION (evidence, not new consent):',
  'Human instruction: <exact relevant quote>',
  'Source: <chat/turn reference>',
  'Destination: workflow agents in ' + REPO,
  'Authorized action: <the task>',
  'Limits: <allowed paths, exclusions, and remaining holds>',
].join('\n');

const BUILD_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['status', 'summary', 'files'],
  properties: {
    status: { type: 'string', enum: ['done', 'question', 'failed'] },
    summary: { type: 'string' },
    question: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
  },
};

const GUARDRAILS = [
  'HARD CONSTRAINTS:',
  '- Only edit files under <ALLOWED_PATHS>. Do NOT touch <FORBIDDEN_LAYERS>.',
  '- Do NOT run git add / git commit / git push. Leave changes unstaged.',
  '- No new runtime dependencies. Match the existing code style.',
  '- If the spec does not decide a fork, return status "question" instead of guessing.',
  'Return status, a concise summary, and each file changed.',
].join('\n');

// ---- Define the work (edit these) ----
const IMPLEMENT_TASK = [
  'You are implementing <FEATURE> in ' + REPO + '. Work from the repo root.',
  '',
  '<Describe exactly what to build, with concrete acceptance criteria and the verify command.>',
  '',
  AUTHORIZATION,
  '',
  GUARDRAILS,
].join('\n');

const VERIFY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['summary', 'issues'],
  properties: {
    summary: { type: 'string' },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['title', 'real', 'detail'],
        properties: {
          title: { type: 'string' },
          real: {
            type: 'boolean',
            description: 'true ONLY for genuine breakage / missing required feature / overclaim',
          },
          detail: { type: 'string' },
        },
      },
    },
  },
};

const FUNCTIONAL_VERIFY = [
  'You did not write this change. Verify it in ' + REPO + ':',
  '1. git status --short and git diff --stat: any file OUTSIDE the allowed paths is a REAL issue.',
  '2. Syntax/lint the changed files. Errors are real issues.',
  '3. Confirm each required feature is present in the code (cite file:line).',
  '4. Run the build/tests: <VERIFY_COMMAND>. Failure is a real issue.',
  'Do not edit files. Return structured issues; real=true ONLY for genuine breakage.',
].join('\n');

const EDITORIAL_VERIFY = [
  'You did not write this change. Review git diff in ' + REPO + ' adversarially:',
  '1. HONESTY: anything that overclaims beyond what was built is real.',
  '2. CORRECTNESS: logic errors, unhandled cases, broken contracts are real; style nits are not.',
  'Read the changed files in full. Do not edit files. Return structured issues.',
].join('\n');

function fixTask(issues) {
  const list = issues.map((i, n) => n + 1 + '. ' + i.title + ' — ' + i.detail).join('\n');

  return [
    'An earlier agent implemented this task in ' + REPO + ':',
    IMPLEMENT_TASK,
    '',
    'Reviewers found these REAL issues in the current unstaged diff. Fix only these:',
    list,
    '',
    GUARDRAILS,
  ].join('\n');
}

// ---- Execute ----
phase('Implement');
const impl = await agent(IMPLEMENT_TASK, {
  label: 'build:impl',
  phase: 'Implement',
  schema: BUILD_SCHEMA,
  model: BUILD_MODEL,
  effort: BUILD_EFFORT,
});

if (!impl || impl.status !== 'done') {
  return { stoppedAt: 'Implement', impl };
}

phase('Verify');
log('Implementer finished; reviewers running: functional + editorial.');
const verdicts = await parallel([
  () =>
    agent(FUNCTIONAL_VERIFY, {
      label: 'verify:functional',
      phase: 'Verify',
      schema: VERIFY_SCHEMA,
      model: FAST_MODEL,
      effort: 'medium',
    }),
  () =>
    agent(EDITORIAL_VERIFY, {
      label: 'verify:editorial',
      phase: 'Verify',
      schema: VERIFY_SCHEMA,
      model: JUDGMENT_MODEL,
      effort: 'medium',
    }),
]);

if (verdicts.length !== 2 || verdicts.some((v) => !v || !Array.isArray(v.issues))) {
  return {
    stoppedAt: 'Verify',
    impl,
    status: 'error',
    summary: 'A required reviewer did not return a valid verdict.',
  };
}

const realIssues = verdicts.flatMap((v) => v.issues.filter((i) => i.real));

let fix = null;

if (realIssues.length) {
  phase('Fix');
  log(realIssues.length + ' real issue(s); a fresh agent is fixing them.');
  fix = await agent(fixTask(realIssues), {
    label: 'build:fix',
    phase: 'Fix',
    schema: BUILD_SCHEMA,
    model: BUILD_MODEL,
    effort: BUILD_EFFORT,
  });
} else {
  log('No real issues found.');
}

return {
  impl,
  verify: verdicts,
  realIssuesFound: realIssues.length,
  realIssues: realIssues.map((i) => i.title),
  fixApplied: Boolean(fix && fix.status === 'done'),
  fix,
};
