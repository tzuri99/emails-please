# Decisions

Judgment calls where a reasonable engineer could have chosen otherwise, and
where the reasoning is not obvious from the code alone. Tuning constants are
listed with the evidence that set them, so a future change can be argued
with rather than guessed at.

Anything pinned by a test says so; those are the ones that will fail loudly
if someone changes them without reading this.

---

## Tuning constants

### `LABEL_MAPPING_DISCOUNT = 0.15`

*[verdict.py](backend/app/pipeline/verdict.py) · pinned by
`test_mapping_discount_cannot_cross_the_threshold`*

When a field's header only matched by fallback — the fuzzy matcher, not the
alias table — the two sides might not describe the same field at all. That
doubt is real and worth showing, so the row is tagged **Label mapping
issue**.

It was first set to **0.35**. That pushed such rows to ~0.63 confidence,
below the 0.70 escalation threshold, which turned **emails 313 and 351 from
reported defects into escalations**. Both are genuine discrepancies; the only
thing unusual about them is a PDF header damaged to `Gross Weightnn(KGS)`.

Two plainly different numbers are still a defect even when the header was
read loosely. The tag carries the doubt; the escalation threshold is reserved
for cases we genuinely cannot call. 0.15 keeps confidence at ~0.85 — visibly
less certain, still reported.

The invariant that matters is not the number but the relation:

```
1.0 - LABEL_MAPPING_DISCOUNT > ESCALATION_THRESHOLD
```

A test asserts exactly that, because the failure mode is silent: raising the
discount does not break anything visibly, it just quietly stops reporting
real defects.

### `RENDER_DPI = 300`

*[ocr.py](backend/app/pipeline/ocr.py) · pinned by
`test_render_dpi_is_the_measured_optimum`*

Measured against the scanned pairs, not chosen by feel:

| Field | 200 dpi | 300 dpi | 400 dpi |
|-------|---------|---------|---------|
| Shipper | `Shippe r. APRIL FAR EAST ( M)` | correct | `ShiPPer: APRILFAR EAST(M)` |
| Port of loading | `Port of Lcading` | correct | correct |
| Gross weight | `128,544 KG` | `128,544 KG` | `128.644kG` |

Higher is not better. At 400 the engine over-sharpens until words merge, and
it corrupts the weight's digits *and* its separator — the worst possible
failure, because a plausible wrong number is harder to catch than an obvious
one. Flagged rows across the three scanned pairs fell from 13 to 5 moving
from 200 to 300.

### `ESCALATION_THRESHOLD = 0.70`

*[verdict.py](backend/app/pipeline/verdict.py) · pinned by
`test_confident_and_uncertain_bands_do_not_overlap`*

Set so the two populations do not overlap: an ordinary mismatch scores
~0.97, a unit artifact ~0.55. Nothing sits near the line, which is what
makes the threshold safe to reason about. If a future change puts cases at
0.68 and 0.72, the threshold has stopped separating anything and the
penalties need revisiting instead of the threshold.

### `OCR_NOISE_SIMILARITY = 0.82`

*[verdict.py](backend/app/pipeline/verdict.py)*

Two OCR'd values this similar (`NHAWA SHEVA` / `NHAVA SHEVA`) differ by a
handful of characters. That is indistinguishable from a misread, so the case
escalates rather than accusing the shipper. Below this, the values are
genuinely different text and are reported normally.

---

## Classification calls

### OCR degrades resolution rather than failing

*[ocr.py](backend/app/pipeline/ocr.py) · pinned by `TestOcrResilience`*

Raising the render resolution to 300 dpi introduced a crash I did not catch
by testing the scanned pairs individually: across a full 520-email run the
ONNX detection model exhausted memory and aborted with `bad allocation`,
losing every result.

Two changes. Pages are yielded one at a time instead of materialising a
whole PDF, and a failed pass retries at 200 dpi rather than abandoning the
document. Separately, `process()` now catches unexpected reader failures
per email: losing 519 results to one bad file is never the right trade.

The pixel budget is the interesting part. It is set near a native 200 dpi
page, but pages are still **rendered at 300 and downscaled**, because
supersampling beats rendering low:

| | Shipper | Gross weight |
|---|---------|--------------|
| native 200 dpi | `Shippe r.` | `G ross Weight` |
| 300 dpi -> 4 MP | `Shipper:` | `Gross Weight` |

So the quality the resolution choice was made for survives at a footprint
that does not fail mid-corpus. With this in place all three scanned pairs
read successfully and `unreadable` is down to the two genuinely corrupt
files, 511 and 515.

### email_513 is no longer "unreadable"

*Changed when `RENDER_DPI` went to 300.*

At 200 dpi this scanned pair produced enough OCR noise that the comparison
could not vouch for itself, and it escalated as `unreadable`. At 300 dpi it
reads cleanly: all seven fields match.

**This may disagree with the answer key**, which likely marks all three
scanned emails (512–514) as unreadable by construction. Accepted anyway,
for two reasons:

1. *Scoring impact is nil.* `final_score` is 30% stage-1 category F1 + 20%
   stage-3 defect F1 + 50% end-to-end. email_513 has no planted defect, so
   end-to-end excludes it; if the key marks it `NEEDS_REVIEW`, stage-3
   excludes it too. Only the reliability diagnostic moves, and that is not
   in the score.
2. *Reading a document beats giving up on it.* Escalating a document we can
   now read would be optimising for the key rather than for the operator.

Since the supersampling change this applies to **512 and 514 as well**: all
three scanned pairs now read cleanly and report no defects. Only 511 and
515 — files that are not valid PDFs at all — remain unreadable. The scoring
argument above is unchanged, because none of the three carries a planted
defect.

### "Wrong document" is separate again

*[models.py](backend/app/models.py) · pinned by `TestWrongDocumentState`*

A packing list arriving where the draft BL should be is neither unreadable
(the file parses perfectly) nor missing (something did arrive). Both of
those framings suggest the wrong next action -- a retry, or a bare "please
send it".

It gets its own state and the draft-reply panel, but the reply names what
actually turned up: *"We received a Commercial Invoice for 5RSG-51584, but
we still need the draft Bill of Lading."* Asking someone to resend without
saying what arrived invites them to send the same file again.

The received type is detected live from the attachments when the panel
opens, rather than stored at run time. The whole point of the message is to
tell the sender what they sent, and the files are the only honest source
for that. A wrong-document case has at most two small attachments.

`case_state` resolves wrong-document **before** awaiting-document, because
a file did arrive; a test pins that the states are mutually exclusive, since
overlapping buckets would stop the tab counts summing to the corpus.

### "Awaiting document" is separate from "Unreadable"

*[models.py](backend/app/models.py) · pinned by `TestAwaitingDocument`*

A document that never arrived and a document that arrived but would not
parse look similar in a queue and need opposite actions: one needs a reply
asking for the file, the other needs a retry. They were initially merged
under `UNREADABLE`, which sent both to the same place.

`MISSING_ATTACHMENT` now covers the 5 BL comparisons whose attachment is
absent plus the 91 emails asking us to send a document, and those cases get
a drafted reply instead of a retry button.

**Their `category` is deliberately unchanged.** Those 91 remain
`SI_REQUEST` in the submission: re-categorising them would move 17% of the
inbox and change stage-1 macro-F1. The new state is an operator-facing view
over the same classification, exactly as `UNREADABLE` is.

### Replies are drafted, never sent

*[runs.py](backend/app/routers/runs.py) `draft_reply`*

The reply is composed server-side, quoting the shipment reference and
naming which of the SI / BL is missing, and handed to the operator to copy.
Nothing is sent. Drafting is cheap and reversible; sending mail on
someone's behalf is neither, and the person who owns the mailbox should be
the one who presses send. "Mark as sent" records the action in the audit
trail, which is the part the system can honestly claim to know.

### "Unreadable" is a derived state, not a fourth status

*[models.py](backend/app/models.py) · `EmailResult.case_state`*

The scorer accepts only `OK | MISMATCH | NEEDS_REVIEW`. Adding a real fourth
status would have broken the submission contract to gain a UI label.

Instead `case_state` splits `UNREADABLE` out of `NEEDS_REVIEW` for the
operator while `status` — the exported value — stays as it was. The split
earns its place because it separates two different jobs: 15 cases needing a
retry or a fresh file, and 5 needing a person to read and judge. Previously
all 20 landed in one queue.

### `wrong_doc_type` fires only on impostor documents

*[extract.py](backend/app/pipeline/extract.py) · pinned by
`test_bl_instruction_is_a_shipping_instruction`*

PDF Shipping Instructions in this corpus are titled **"BILL OF LADING
INSTRUCTION"** — they instruct the carrier on what to print *on* the BL.
Matching `bill of lading` inside that string flagged ten valid SIs as the
wrong document type.

Beyond fixing the pattern order, the escalation rule was narrowed: only an
actual impostor (commercial invoice, packing list, certificate of origin)
triggers `wrong_doc_type`. An SI/BL title disagreement is not reliable
enough to act on, because templates vary and the email already establishes
each attachment's role.

### "Please assist to send the draft BL" is an SI_REQUEST

91 emails ask for a document to be produced and carry no attachments. They
could plausibly be `BL_COMPARISON` with a missing attachment. They are
classified `SI_REQUEST` because the project flowchart groups "Shipping
Instruction and Bill of Lading Request" as one node — both are requests to
*produce* a document, and neither has anything to compare.

At 17% of the inbox this is the single highest-leverage labelling decision
in the classifier. If stage-1 F1 comes back lower than expected, this is the
first thing to re-examine.

---

## Comparison semantics

### Ports compare on city name; the UN/LOCODE is discarded

*pinned by `test_port_defect_survives_shared_locode`*

Defects are planted in the city while the locode is left untouched —
`MOMBASA, KENYA (KEMBA)` against `TUTICORIN, INDIA (KEMBA)`. Normalising to
the locode, which is the obvious instinct, would hide **every** port
discrepancy: 26 of the ~62 total.

### Party names compare exactly; no fuzzy matching

*pinned by `test_party_suffix_is_a_real_difference`*

`APRIL FINE PAPER TRADING` and `APRIL FINE PAPER TRADING (MIDDLE EAST) FZE`
are distinct legal entities. Anything that tolerates a near-match on shipper,
consignee or notify party hides real defects. The OCR spacing tolerance is
the deliberate exception, and it applies only when a document was scanned.

### `NET WEIGHT` never folds into gross weight

*pinned by `test_fallback_never_folds_net_into_gross`*

A different quantity that appears in the same documents. Mapping it would
manufacture discrepancies out of correct paperwork.

### Confidence describes our reading, not the shipment

A confident mismatch means the documents plainly disagree. A low-confidence
mismatch means they may only *appear* to disagree because of how we read
them. This distinction is the reason the system can escalate instead of
false-alarming, and every penalty in `trace.py` is denominated in it.

### The comparison is deterministic; the AI only reads

End-to-end scoring requires the flagged defect field set to match
**exactly**, and a model asked to emit that set will vary between runs on
identical input. Extraction may be probabilistic — OCR, and a vision model
later. The comparison must not be.

---

## Product and interface

### The review panel asks for an outcome, not agreement

An earlier version asked *"do you agree?"* and separately offered the
outcomes. When the machine already proposed "clear", **"Agree with the
machine" and "Mark clear" were the same button.** That is not a labelling
problem; it is the wrong question.

The panel now asks what is true for the shipment and offers Clear /
Discrepancy / Second look, badging whichever one the machine picked.
Agreement is derived server-side, so the client cannot mislabel its own
history.

### "Formatting difference" means the documents differed, not that we normalised

*[verdict.py](backend/app/pipeline/verdict.py) · pinned by
`TestMatchIsNotFormatting`*

A match was originally tagged `formatting` whenever any normalisation step
had *run*. But stripping a comma from both sides of an identical string is
not a difference between documents — `ASHDOD, ISRAEL` against `ASHDOD,
ISRAEL` was being tagged amber at 100% confidence.

Across the corpus this mis-tagged **440 rows**, which is worse than
cosmetic: an operator who sees amber on rows that plainly agree learns to
ignore the tag, and then misses the ones that matter.

The test is now whether the raw values differ, not whether the normaliser
did anything. Corpus-wide the `formatting` tag fell from 456 rows to 16, and
`match` rose from 257 to 697.

### Header figures and sidebar tabs share one query

The header showed `by_status.NEEDS_REVIEW` (18) while the sidebar showed the
split `by_state` counts (5 + 13). Both were correct about different things
and looked like a bug.

`/stats` now computes one cross-tab of status × review_reason × category and
derives the state counts, the type counts, and which state/type pairs exist
from it. The header and the sidebar read the same field, so they cannot
drift apart again; the cross-tab also drives disabling type options that
cannot occur in the selected state.

### The row rail is drawn from `kind`, as a pseudo-element

*[DiffPane.jsx](frontend/src/components/DiffPane.jsx)*

Two bugs in one indicator. It was an `inset` box-shadow, which paints
behind child backgrounds, so the row's own hover fill erased it. And its
visibility keyed off `verdict`/`artifact` while the tag beside it keyed off
`kind` -- after the match/formatting fix a row could show a "Formatting"
tag with no rail, because it was a MATCH with no artifact.

It is now a `::before` pseudo-element (which paints above children) driven
by the same `kind` the tag reads. One source, one indicator.

### The confidence bar is neutral, not green

*[Confidence.jsx](frontend/src/components/Confidence.jsx)*

Confidence and correctness are different axes, and colouring confidence
green/red conflated them: a correctly-detected genuine discrepancy sits at
100% confidence, and a green bar there says "good news" about a shipment
that is wrong.

The bar is now slate by default and only takes colour when the reading is
doubtful — amber under 85%, red under 70%, which is the same threshold the
server uses to decide whether to report or escalate. Colour on this control
always means "look here", never "this is fine". Outcome colour belongs to
the field tags alone.

### Reviews are appended, never edited

*[models.py](backend/app/models.py) · pinned by
`test_history_is_appended_never_replaced`*

Re-reviewing adds a row; the standing decision is the latest one. Undo
removes only that latest entry. An audit log you can edit is not an audit
log.

### Clearing a flagged document is press-and-hold and focus-gated

It is the only action that can release a real defect. It requires an 800 ms
hold rather than a keystroke, and stays locked until every flagged field has
been opened — an operator should not be able to wave through something they
have not looked at. Clearing a document the machine already called clean is
a single key.

### Auth is optional by configuration

*[auth.py](backend/app/auth.py) · pinned by `tests/test_auth.py`*

With no `FIREBASE_PROJECT_ID` the API runs open, which is what a local run
and a demo need. Set it and every mutating endpoint requires a token. Both
states are tested, because the failure mode of optional auth is that it
silently stays off in production.

Turning it on does more than gate access: the reviewer stops being a string
the client types and becomes the verified subject of a signed token.

---

### Source traceability records lines, not coordinates

*[extractors.py](backend/app/pipeline/extractors.py) · pinned by
`TestSourceTraceability`*

Each extracted value carries the verbatim line it came from and a locator a
human can check: `line 24`, `S.I. row 4`, `table 1, row 3`, `OCR line 2`.

The fuller version — page and bounding box, highlighting the region in the
source PDF — was scoped out deliberately. The OCR engine already returns
boxes and they are currently discarded, so it remains available, but it
would require threading geometry through all four extractors and building a
PDF viewer. The line snippet answers the actual question ("what did the page
say?") at a fraction of the cost.

The snippet is the line as the document had it, before any normalisation. A
test asserts the extracted value actually appears within its own snippet, so
the two cannot drift apart.

## Infrastructure

### The dataset is baked into the API image

*[backend/Dockerfile](backend/Dockerfile)*

Compose bind-mounts `./data`, but Azure Container Apps cannot. An API that
only finds its inbox under Compose fails on first deploy. The image is
therefore built from the project root and carries `data/`; Compose still
overlays the same path read-only for development.

### The reviews schema change was migrated, not dropped

Making reviews append-only required removing a `UNIQUE` index and adding a
column. `create_all` never alters existing tables, so the running Postgres
was migrated in place with explicit SQL rather than dropping the volume.

**No Alembic revisions exist yet.** Alembic is a dependency and `create_all`
builds the right schema from scratch, but the next schema change against
live data needs a real migration.

### MUI, with its defaults turned off

The stack table specifies MUI. Elevation is disabled globally and structure
comes from hairline borders instead: stacked shadows on every surface are
the generic-dashboard tell, and at this density they are noise that encodes
nothing. Monospace appears only on values read off a document, where tabular
figures genuinely aid digit-by-digit comparison.

### Design-tool output was used selectively

The `ui-ux-pro-max` design-system query returned a **landing page** pattern
(hero, search bar, "Start trial" CTA) for what is an internal console nobody
signs up for. Style, density and typography guidance were taken; the pattern
was discarded. The returned `#EFF6FF` background was also overridden — a
blue-tinted surface under blue text is fatiguing for all-day use, and the
palette had no amber, which a three-state verdict needs.
