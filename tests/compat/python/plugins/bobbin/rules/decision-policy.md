# Decision capture policy

Run only on core's choice signal. Reuse Current while scope, anchor, and actual body remain. With known scope/key, run one exact-slot `check`; otherwise discover. Reuse returned actual Decision, Rationale, Rejected alternatives, and non-empty Revisit conditions that turn without another context read.

Classify `new|same|supporting|rationale_changed|conflict` by meaning, not hash/ID/metadata. Reuse `same|supporting` quietly. Before conclusion, quote each returned non-empty actual Decision, Rationale, Rejected alternatives, and Revisit conditions section. State the revisit token verbatim: `satisfied|no evidence|ambiguous`. `satisfied` needs facts establishing the stored condition; the requested conflicting action is not evidence. `no evidence` means no facts or another concern; `ambiguous` means relevant facts incomplete/conflicting.

Hold the affected action: no code, file, or command change may advance it before the answer. Ask one explicit binary question. Keep means the action is not performed; supersede permits it only after that explicit choice. A satisfied revisit authorizes reassessment, not implementation. The explicit choice settles that decision payload and authorizes capture without a second storage question.

Then propose one mature choice per milestone with scope/commitment. Do not re-propose dismissed/deferred without new evidence. Context-decision drafts; core writes.

Use core's active language. Follow the shared [recording policy](../skills/context/references/recording-policy.md) for `explicit|auto|adaptive`. Disabled owners do not participate automatically. A genuine user choice remains necessary for DEC in every mode; policy authorization is not commitment evidence. Run `record` with user or policy authorization: internal preview, unchanged apply in the same response, `approval_digest` private. On semantic delta, hold the write. Never regenerate after approval.
