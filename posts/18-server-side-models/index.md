# 18 · Project 3 · Server-side model calls and multi-page research

> **TL;DR.** Sampling, where a Model Context Protocol (MCP) server asked the host's model to
> generate text for it, is deprecated in revision 2026-07-28 and there is no server-to-client
> request channel left to carry it. A server that needs a model now calls a provider directly,
> which moves three things onto the server operator: the bill, the consent, and the prompt.
> This post rebuilds the research browser's second half around that shift, with a summarizer
> that works with no model at all and a report in which a claim without a source cannot exist.
>
> **After reading this you will be able to:**
> - Explain precisely what happened to sampling, and what survives of it.
> - Build a server-side summarizer as a protocol with an offline default and an opt-in provider.
> - Report which summarizer actually ran, so a fallback is never labelled as the model.
> - Run a bounded multi-page research pass whose every claim carries the page it came from.

![Two panels. On the left, the old sampling path: a server sends a request up through the client to the host that owns the model, with both legs struck through because revision 2026-07-28 has no channel to carry them, and a dashed billing boundary drawn around the host. On the right, the replacement: the client still calls the server, and the server reaches a model provider directly over HTTPS with its own key, with the billing boundary redrawn around the server and the provider. A table underneath answers who pays, who consented, and who answers for the prompt.](diagrams/01-sampling-vs-direct-call.svg)
*The mechanism changed. So did the answer to three questions that are not about mechanism at all.*

---

## 1. The post that had to be rewritten

[Post 17](../17-research-browser/index.md) built a browsing server that turns a page into
article text and reports the byte counts to prove it. On the article fixture that is 76,201
bytes of markup down to 4,126 bytes and 707 words. Good, and not enough. Seven hundred words
per page across five pages is still three and a half thousand words of reading for a question
that might be answered in three sentences.

So the server wants a model of its own. Not the host's model answering the user, a model to do
the server's own summarizing work before the result is ever returned.

The previous edition of this post answered that with **sampling**, and the answer was elegant:
the server called back through the MCP session, the host's model generated the summary, and
nobody needed an API key. That post opened with the line "here is the mind-bending part: what
if the server could ask the model for help?"

That mechanism is gone. Not softened, not discouraged. The back channel it travelled over does
not exist in revision 2026-07-28, and the feature itself is formally deprecated. This is the
largest single correction in this series, and the rest of the post is what replaces it.

Vocabulary first, because readers arrive mid-series. The **host** is the application the user
talks to and the thing that owns the model and the conversation. The **client** is the
protocol-speaking object inside the host, one per connected server. The **server** is the
process this project builds. A **provider** is a model vendor's own Hypertext Transfer Protocol
(HTTP) application programming interface (API), reached with a key, and outside MCP entirely.

## 2. What happened to sampling, precisely

Two separate things happened, and conflating them causes confusion later.

**The feature is deprecated.** SEP-2577 deprecates roots, sampling, and logging in revision
2026-07-28. The deprecated registry gives sampling's migration path in five words: "Integrate
directly with LLM provider APIs." The earliest removal is the first revision released on or
after 2027-07-28, so the types are still in the schema and still work where they can be
reached. `ClientCapabilities.sampling`, `ModelPreferences`, `ModelHint`, `ToolChoice`, and
`SamplingMessage` all carry `@deprecated`.

**The transport it used was removed, with no deprecation window.** Revision 2026-07-28 has no
server-initiated JSON-RPC requests on any stream. That is not a deprecation, it is a deletion,
and it is the part that actually breaks code. The Python software development kit (SDK) is
blunt about it. `mcp/shared/exceptions.py` defines:

```python
class NoBackChannelError(MCPError):
    """Raised when sending a server-initiated request over a transport that
    cannot deliver it."""
```

and the modern Streamable HTTP path hardcodes `can_send_request: bool = False`. The stateless
manager sets it false as well. There is nothing to call back to, and calling anyway raises
rather than hanging.

**What survives.** `sampling/createMessage` still exists as a `CreateMessageRequest`, but only
inside the Multi Round-Trip Requests (MRTR) mechanism that
[Post 08](../08-elicitation-and-mrtr/index.md) covers. The server does not call the client. It
*returns* a request and waits to be called again. This is the shape of that result, assembled
from the schema's field names rather than captured from a run, since this project deliberately
does not implement the path:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "resultType": "input_required",
    "inputRequests": {
      "summary": {
        "method": "sampling/createMessage",
        "params": {
          "messages": [ { "role": "user", "content": { "type": "text", "text": "..." } } ],
          "maxTokens": 800
        }
      }
    },
    "requestState": "<opaque, server-minted>"
  }
}
```

That is a real path and it is not the one to build on. It requires the client to declare the
deprecated `sampling` capability, it costs a full extra round trip through the host, the host
may decline, and the whole feature is scheduled for removal. Note also that
`includeContext: "thisServer"` and `"allServers"` were deprecated a revision earlier, in
2025-11-25; `"none"` is the default and is the only value worth writing.

If you are maintaining a server that already ships sampling, that MRTR path is your migration
runway. If you are writing a new one, do not adopt it.

**Changed in 2026-07-28.** The SDK marks the old call site too:
`ServerSession.create_message.__deprecated__` reads "The sampling capability is deprecated as
of 2026-07-28 (SEP-2577)." Your editor will tell you before the specification does.

## 3. Who pays, who consented, who answers for the prompt

Here is the part that matters more than the mechanism, and the part the previous edition never
had to think about, because sampling made all three questions somebody else's problem.

**Who pays for the tokens.** Under sampling, the host did. The user's own subscription or the
host operator's API budget absorbed the cost, and the server author never saw an invoice.
Calling a provider directly moves that line: the tokens are billed to whoever supplied the key
the server is holding. If you deploy this server for a team, you are buying the summaries. A
model call inside a tool has a unit cost per invocation, and a research pass that visits five
pages makes five of them. Budget for it explicitly, and read section 7 on why that budget
belongs in the code and not in a wiki page.

**Who consented to the prompt.** Under sampling the specification said hosts SHOULD prompt the
user before allowing a sampling request, so there was a human in the loop, at least in
principle. Calling a provider directly removes that human. The user asked the host to research
a topic; they did not agree that a second model, at a vendor they may not have chosen, would
receive the text of every page the server fetched. Consent moved from the user at call time to
the operator at deploy time, and those are different people making a different decision.

Two consequences follow, and neither is optional. **Disclose the provider in your server's
documentation**, by name, so an operator knows whose terms apply before they set a key. And
**make the provider path opt-in rather than opt-out**, so an operator who never configured a
model never silently sends page text anywhere.

**Who answers for the prompt.** This one does not move, which is exactly why it is worth
saying. The system prompt in this server reads:

```python
_SYSTEM_PROMPT = (
    "You summarize web pages for a research assistant. Preserve concrete facts, "
    "numbers, names, and conclusions. Do not add information that is not in the "
    "text. Do not use emoji. Write plain prose, no headings and no bullet lists."
)
```

The server author wrote that under sampling and writes it now. If it instructs a model to
editorialize, flatten a caveat, or drop an attribution, the resulting summary carries the
server author's judgment into the user's answer under the host's branding. Sampling never
insulated you from that, and the direct call does not create it. What the direct call changes
is that the prompt is no longer visible to the host, so nobody downstream can review it. Put it
in a constant, keep it in version control, and treat a change to it as a change to behavior.

## 4. A protocol, and a default that needs no model

[summarize.py](../../code/17-research-browser/src/research_browser/summarize.py) starts from a
two-member protocol. That is the entire seam between "this server summarizes" and "this server
has an opinion about which vendor you pay":

```python
class Summarizer(Protocol):
    name: str

    async def summarize(self, text: str, *, focus: str | None = None,
                        max_sentences: int = DEFAULT_MAX_SENTENCES) -> str: ...
```

The default implementation needs no network, no key, and no budget. `ExtractiveSummarizer`
ranks the document's own sentences by term frequency and returns the best few, verbatim:

```python
frequencies: Counter[str] = Counter()
for sentence in sentences:
    frequencies.update(_content_words(sentence))
peak = max(frequencies.values())

for index, sentence in enumerate(sentences):
    words = _content_words(sentence)
    score = sum(frequencies[w] / peak for w in words) / math.sqrt(len(words))
    if focus_terms:
        score *= 1.0 + 0.5 * len(focus_terms.intersection(words))
    scored.append((score, index))
```

It is worse than a model, and it has four properties a generated summary does not.

**It is always available.** A server whose only summarization path requires a credential is a
server that does nothing on a machine that has never seen one. There is a test whose whole job
is that sentence: `default_summarizer(env={})` returns the extractive one.

**It is deterministic.** Ties resolve by document position, so identical input gives identical
output. A summarizer that returns different text for the same page cannot be tested and cannot
be cached.

**It preserves document order.** The chosen sentences come back in the order they appeared, so
the summary reads as an abridgement rather than a shuffle.

**Every sentence provably appeared on the page.** This is the one section 8 depends on. An
extractive claim can be checked against the source text; a generated one cannot.

One detail in that implementation is load-bearing and easy to get wrong. Sentences are joined
with a newline, not a space:

```python
# One sentence per line, not space-joined. Headings and list items have
# no terminating punctuation, so a space-joined summary cannot be split
# back into the sentences it was built from.
return "\n".join(sentences[i] for i in chosen)
```

Headings and list items have no full stop. Join them with a space and "Where the bytes actually
go The article is not the page." becomes one unsplittable string, and the citation step that
needs to attribute each sentence individually has nothing to work with.

## 5. The optional provider path

`AnthropicSummarizer` is the opt-in half. Nothing in it is reachable unless the operator
installed the extra and set a key, which is the point.

```python
message = await self._require_client().messages.create(
    model=self.model,
    max_tokens=self.max_tokens,
    system=_SYSTEM_PROMPT,
    output_config={"effort": "low"},
    messages=[{"role": "user", "content": f"{instruction}\n\n{text}"}],
)
```

Three details in that call are current-API specific and worth knowing rather than copying
blind. `effort` lives inside `output_config`, not at the top level, and it is where thinking
depth is controlled now; a summary does not need deep reasoning. `max_tokens` covers thinking
plus visible text on this model line, so it needs headroom rather than being sized tight around
the answer. And there is no `temperature` and no `top_p`, because the sampling parameters were
removed on this model line and sending one is a `400`. There is a test that asserts their
absence from the request, which is cheaper than rediscovering it in production.

Reading the reply has its own trap:

```python
parts = [block.text for block in message.content
         if getattr(block, "type", None) == "text"]
```

`content` is a list of blocks and the first one is not guaranteed to be text. Filter by type
rather than indexing, and there is a test that inserts a non-text block in front to prove it.

Selection is by environment, and the order is deliberate:

```python
forced = (env.get(FORCE_ENV) or "").strip().lower()
if forced == "extractive":
    return ExtractiveSummarizer()

api_key = (env.get(API_KEY_ENV) or "").strip()
if not api_key and forced != "anthropic":
    return ExtractiveSummarizer()
```

An explicit `RESEARCH_BROWSER_SUMMARIZER=extractive` beats a configured key, so a deployment can
turn the provider off without unsetting credentials the rest of the box needs. And a key
present with the package missing logs a warning and degrades, rather than taking the server
down at import time over an optional dependency.

The test suite pins the extractive summarizer for every test through an autouse fixture. That
is not tidiness. Without it, a developer who happens to have a key exported runs a different
code path than continuous integration does, and possibly a billable one.

## 6. Never label a fallback as the model

The first edition of this post shipped a defect that is worth more attention than any of the
protocol changes, because it is the kind that survives review.

Its summarizer wrapped the sampling call in `try`/`except`. On failure it fell through to
truncating the text at two thousand words. Then the calling tool formatted the result and
appended a footer:

```
---
*Summarized via MCP Sampling. Use `browse_url` for full content.*
```

The footer was printed on the failure path too. A caller receiving that text had a summary
labelled as the product of a model, when in fact it was the first two thousand words of the
page with the rest thrown away.

That is not a cosmetic bug. Consider what a reader does with each answer. Given a model summary
they weigh it as a compression of the whole page, so a fact absent from it is a fact the model
judged unimportant. Given a truncation they know the tail is simply missing. The label decides
which of those two readings is correct, and a wrong label makes the correct reading
unavailable. Worse, the failure is silent and repeatable: an expired key produces plausible
output forever, and nothing in the result says so.

So `Summary` records what actually ran, next to what was asked for:

```python
@dataclass
class Summary:
    text: str
    summarizer: str      # what actually ran
    requested: str       # what was asked for
    fell_back: bool
    note: str
    input_words: int
    summary_words: int
    reduction_percent: float
```

and the wrapper fills it honestly:

```python
try:
    summary_text = await summarizer.summarize(text, focus=focus, max_sentences=max_sentences)
    ran = requested
except Exception as exc:
    fell_back = True
    note = f"{requested} failed ({type(exc).__name__}: {exc}); used extractive"
    fallback = ExtractiveSummarizer()
    summary_text = await fallback.summarize(text, focus=focus, max_sentences=max_sentences)
    ran = fallback.name
```

Here is a real run of that path, from a provider adapter driven by an injected client that
raises:

```json
{
  "text": "Extraction removes the boilerplate from a web page.\nA cache avoids paying for extraction twice in one session.",
  "summarizer": "extractive",
  "requested": "anthropic:claude-opus-5",
  "fell_back": true,
  "note": "anthropic:claude-opus-5 failed (RuntimeError: 401 invalid x-api-key); used extractive",
  "input_words": 41,
  "summary_words": 18,
  "reduction_percent": 56.1
}
```

Everything a caller needs is in there: which one ran, which one was wanted, that a fallback
happened, and why. The successful path is the same shape with `summarizer` and `requested`
equal and `note` empty. The rule generalizes past summarization. **If your tool has more than
one way to produce its answer, the result must say which one produced it**, and the field must
be part of the published output schema rather than a sentence in a text block.

## 7. Multi-page research: the loop and the budget

![A call to research_urls removes duplicate URLs, then enters a loop drawn as a dashed container. Each pass starts at a budget gate checking pages visited against the page limit and words read against the word limit; failing either leaves the loop with a message naming the limit and how many sources went unvisited. Inside the loop a page is loaded, summarized, and recorded with its citations, and a page that will not load is written to a failures list rather than ending the pass. Five labelled exits converge on one report whose stopped_because field says which happened.](diagrams/02-research-loop.svg)
*Every exit from the loop is named in the result, so "it finished" and "it ran out" are told apart.*

One page at a time is a browsing server. Several pages, a budget, and an audit trail is a
research server.
[research.py](../../code/17-research-browser/src/research_browser/research.py) is the
difference.

**A budget is not optional when a model is involved.** An unbounded loop over a list of
model-supplied URLs is a way to spend an afternoon, a context window, and somebody's API
balance. Three ceilings, in one dataclass:

```python
@dataclass
class Budget:
    max_pages: int = DEFAULT_MAX_PAGES
    max_words: int = DEFAULT_MAX_WORDS
    max_claims_per_page: int = DEFAULT_CLAIMS_PER_PAGE
```

The gate runs at the top of each iteration, and when it trips it says exactly what tripped it:

```python
if len(sources) >= budget.max_pages:
    stopped_because = (
        f"budget: page limit reached ({budget.max_pages}), "
        f"{len(ordered) - index} source(s) not visited"
    )
    break
```

Real output from two runs over the same three fixture URLs, differing only in the budget:

```
stopped_because: budget: page limit reached (2), 1 source(s) not visited
stopped_because: budget: word limit reached (500), 2 source(s) not visited
```

Compare that against a report that simply contains two sources. Those are the same artifact and
different facts. "I read everything you gave me" and "I ran out of budget and skipped one" call
for different next actions from the caller, and a report that cannot distinguish them forces
the caller to guess.

**A dead link does not end the pass.** One unreachable URL in a list of five is a normal
Tuesday:

```python
try:
    extraction, from_cache = await load_page(url)
except Exception as exc:
    failures.append(f"{url}: {exc}")
    log.warning("research: skipping %s (%s)", url, exc)
    continue
```

The failure is recorded and named, and the pass continues. A report that says which sources
failed is more useful than one that quietly has fewer sources than you asked for.

**Duplicates are removed, order is kept.** A duplicate URL is a wasted page against the budget
and a duplicated bill. Dedupe, but keep the caller's order, because the first link is usually
the one they trust most.

Two smaller decisions round it out. This loop lives inside a single `tools/call` rather than
using the Tasks extension from [Post 09](../09-tasks/index.md), which is the right choice for
five pages and the wrong one for five hundred; when a pass will outlive a request, promote it
to a task. And every page goes through the same `load_page` from post 17, so the cache is
shared: a URL already browsed in this session costs no second navigation and no second
extraction.

## 8. Every claim carries its URL

![Three panels. The first is a page as the extractor left it, with its URL, its title, and one sentence outlined to mark the sentence the summarizer chose. The second is the citation log, whose add method checks that the claim is not empty, that the URL is not empty, and whether the claim appears verbatim in the source text; a branch below shows an empty URL raising rather than being filtered out later. The third is the resulting citation in the report, carrying claim, URL, title, and verbatim flag. A return path shows any claim being walked back to the sentence it came from.](diagrams/03-citation-provenance.svg)
*Provenance enforced by the type rather than promised in a docstring.*

A research report whose claims cannot be traced is a rumor with formatting. The usual way to
handle that is a rule in the prompt: "include citations for each source." That is a request,
and the model may decline it.

This project makes it a property of the code instead. There is exactly one way a claim enters a
report, and it refuses:

```python
def add(self, claim: str, *, url: str, title: str, source_text: str = "") -> Citation:
    claim = claim.strip()
    if not claim:
        raise MissingSourceError("a claim cannot be empty")
    if not url.strip():
        raise MissingSourceError(
            f"refusing to record a claim with no source URL: {claim[:60]!r}"
        )
```

Note that it raises rather than logging and skipping. An unsourced claim is never recorded and
then filtered out later, because there is nothing to filter. The report is assembled only from
`citations.entries`, so no code path exists by which an unattributed claim reaches the caller.

The `verbatim` flag is verified rather than asserted:

```python
verbatim=bool(source_text) and claim in source_text,
```

The extractive summarizer returns sentences that appeared on the page, so its claims come back
`verbatim: true` and are quotable. A model's paraphrase is still cited, and is recorded with
`verbatim: false`. Both are attributable; only one can be put inside quotation marks. A test
pins each half, including the case where the claim genuinely is not in the source.

Here is one citation from a real run over the two article fixtures:

```json
{
  "claim": "The regulator has opened a twelve-week consultation on how data portability obligations should apply to platforms above the designated user threshold.",
  "url": "https://example.com/news/portability-consultation",
  "title": "Regulator opens consultation on data portability rules",
  "verbatim": true
}
```

And the report header from the same run, with `sources` and `citations` elided:

```json
{
  "topic": "page weight",
  "summarizer": "extractive",
  "pages_visited": 2,
  "pages_failed": 0,
  "words_read": 841,
  "words_returned": 113,
  "reduction_percent": 86.6,
  "stopped_because": "completed: visited every source",
  "failures": []
}
```

Six citations came back with that header, three per page, and each `sources` entry carried its
own `raw_bytes`, `clean_bytes`, `reduction_percent`, and the summarizer that produced its
summary. Stack the two reductions and the shape of the whole project appears: 319,329 bytes of
markup became 841 words of article, and 841 words of article became 113 words of claims, every
one of them carrying the page it came from.

## 9. Running it

The suite needs no network, no API key, and no browser binary:

```bash
cd code/17-research-browser && PYTHONPATH=src pytest tests -q
```

```
........................................................................ [ 94%]
..s.                                                                     [100%]
75 passed, 1 skipped in 1.92s
```

The single skip is `test_a_key_selects_the_provider_when_the_sdk_is_installed`, which uses
`pytest.importorskip` and is skipped because the optional provider extra is not installed here.
That is the intended state, and its sibling test asserts the other half: a key set with the
package missing degrades to the extractive summarizer rather than crashing.

The provider is opt-in at install time as well as at configuration time:

```toml
[project.optional-dependencies]
provider = ["anthropic>=0.60"]
```

```bash
uv sync                      # no provider, extractive summarizer, works
uv sync --extra provider     # provider available, still inactive without a key
export ANTHROPIC_API_KEY=...  # now the provider path activates
```

Post 18 adds two tools to the five from post 17:

```
summarize_page   inputs=['url', 'focus', 'max_sentences']                outputSchema=yes  readOnly=True
research_urls    inputs=['topic', 'urls', 'focus', 'max_pages', ...]     outputSchema=yes  readOnly=True
```

A real `summarize_page` result against the article fixture, with the summary text elided:

```json
{
  "url": "https://example.com/blog/what-a-page-costs",
  "summarizer": "extractive",
  "requested_summarizer": "extractive",
  "fell_back": false,
  "note": "",
  "page_words": 707,
  "summary_words": 50,
  "reduction_percent": 92.9,
  "from_cache": false
}
```

Seven hundred and seven words to fifty, with the summarizer named in the result rather than in
the prose.

## 10. When not to call a model from your server

Three cases where the honest answer is that the server should not have a model at all.

**When the host's model can do it in the same turn.** If your tool returns two thousand words
and the host's model is about to read them anyway, summarizing them first spends money to
discard information the reader already paid for. Summarize when you are visiting many pages and
most of them will not matter, not when you are returning one page to a model that asked for it.

**When you cannot disclose the provider.** If the deployment cannot tell its users that page
text leaves for a third party, do not make the call. Ship the extractive path and say so. A
worse summary that everyone understands beats a better one nobody agreed to.

**When determinism matters more than quality.** A generated summary varies between runs, which
makes downstream caching, diffing, and regression testing harder. The extractive path is
reproducible, and for a pipeline that is compared against yesterday's output that property can
outweigh the prose.

One honest note on the specification. SEP-2577 states that during the deprecation period
wire-level behavior is unchanged and no existing implementations break. That is true of the
*deprecations*. It is not true of the revision as a whole: the removal of server-initiated
requests is a separate, window-free deletion, and it is what actually breaks a sampling-based
server. Read the two facts separately or the timeline will look more relaxed than it is.

---

## Common pitfalls

- **Reaching for `ctx.session.create_message()` and trusting the deprecation timeline.** The
  feature is deprecated with removal no earlier than 2027-07-28, but the transport it used was
  deleted outright. On the modern Streamable HTTP path `can_send_request` is hardcoded false
  and the call raises `NoBackChannelError`. The twelve-month window protects the types, not
  your call site.
- **Labelling a fallback as the thing that failed.** Catching a provider exception, returning
  truncated text, and keeping the "summarized by the model" wording is the defect this module
  exists to not have. Record what ran, what was requested, and why they differ, in the
  structured result rather than in prose.
- **Making the provider the only path.** A server whose summarization requires a credential
  does nothing on a machine that has never seen one, and its test suite cannot run in
  continuous integration. Build the offline default first and make the provider an extra.
- **Letting a developer's exported key change what the tests exercise.** Pin the summarizer in
  an autouse fixture. Otherwise the suite passes locally on a code path that never runs in
  continuous integration, and may quietly bill somebody.
- **Space-joining extracted sentences.** Headings and list items carry no terminating
  punctuation, so a space-joined summary cannot be split back into the sentences it was built
  from, and per-sentence citation becomes impossible. Join with a newline.
- **Asking for citations in the prompt instead of enforcing them in the type.** A prompt rule
  is a request the model may decline. Make the log refuse an unsourced claim, and assemble the
  report only from the log.
- **Running a research loop with no ceiling and no stop reason.** Pages, words, and failures
  all need limits, and the report has to say which limit stopped it. "It finished" and "it ran
  out of budget" are different answers and produce identically shaped output otherwise.
- **Sending `temperature` or `top_p` to a current model line.** They were removed and the
  request comes back `400`. Control depth with `effort` inside `output_config`, and remember
  `max_tokens` covers thinking as well as visible text.

---

## Further reading

- Specification, *"Deprecated features"*, revision 2026-07-28. The registry entry for sampling,
  its SEP-2577 deprecation, and the migration path.
  <https://modelcontextprotocol.io/specification/draft/deprecated>
- Specification, *"Sampling"*, revision 2026-07-28. Still present, still documented, marked
  deprecated throughout.
  <https://modelcontextprotocol.io/specification/draft/client/sampling>
- SEP-2577, *"Deprecate roots, sampling, and logging"* (2026). Read its own caveat that
  wire-level behavior is unchanged during the deprecation period, then read the changelog entry
  removing server-initiated requests.
  <https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2577>
- Anthropic API documentation. Model identifiers, `output_config.effort`, and the removal of
  the sampling parameters on the current model line.
  <https://platform.claude.com/docs>
- MCP Python SDK, `mcp==2.0.0b2`. `NoBackChannelError`, the `__deprecated__` marker on
  `ServerSession.create_message`, and every result quoted in this post, driving
  [code/17-research-browser/](../../code/17-research-browser/).

Full citations in [REFERENCES.md](../../REFERENCES.md).

---

## What to read next

- **[Post 19 — Security: the attacks the protocol does not stop](../19-security/index.md)**:
  this server fetches any Uniform Resource Locator a model hands it and pipes the result into a
  second model's prompt, which is two attack surfaces in one tool.
- **[Post 17 — Project 3 · A deep research browser](../17-research-browser/index.md)**: the
  first half of this project, where the extraction and the byte counts this post builds on come
  from.
- **[Post 08 — Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md)**:
  the mechanism that replaced the server-to-client channel, and the only place a
  `CreateMessageRequest` can still travel.
