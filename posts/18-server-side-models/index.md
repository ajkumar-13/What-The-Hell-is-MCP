# 18 - Project 3: Server-side model calls and multi-page research

> **TL;DR.** Sampling, where a Model Context Protocol (MCP) server asked the host's model to generate text for it, is deprecated in revision 2026-07-28, and the server-to-client request channel that carried it is gone, so a server that needs a model now calls a provider directly and takes on three things the host used to carry: the bill, the consent, and the prompt. This post rebuilds the second half of the research browser around that shift, with a summarizer that works with no model at all, a result that always names the summarizer that actually ran, and a multi-page report in which a claim without a source cannot exist. Every frame, result and log line below, apart from the first edition's footer quoted in section 6, is printed by a script in `snippets/` against `mcp==2.3.0` and the project's committed fixtures, or by the project's own test suite; nothing here contacts a model provider.
>
> **Prerequisites:** [Post 17](../17-research-browser/index.md).
> **Safe to skip?** Skip it if the reader can already say what is left of sampling in revision 2026-07-28, build a component with an offline default and an opt-in paid path that reports which one ran, and bound a loop over model-supplied Uniform Resource Locators (URLs) so that its result says why it stopped.
>
> **After reading, you will be able to:**
>
> - Explain what happened to sampling in revision 2026-07-28 and what survives of it.
> - Build a server-side summarizer as a protocol with an offline default and an opt-in provider.
> - Report which summarizer actually ran, so that a fallback is never labeled as the model.
> - Run a bounded multi-page research pass whose every claim carries the page it came from.

![Two panels. On the left, the old sampling path: a server sends a request up through the client to the host that owns the model, with both legs struck through because revision 2026-07-28 has no channel to carry them, and a dashed billing boundary drawn around the host. On the right, the replacement: the client still calls the server, and the server reaches a model provider directly over Hypertext Transfer Protocol Secure (HTTPS) with its own key, with the billing boundary redrawn around the server and the provider. A table underneath answers who pays, who consented, and who answers for the prompt.](diagrams/01-sampling-vs-direct-call.svg)

*The mechanism changed, and so did the answers to two of three questions that are not about mechanism at all.*

---

## 1. The question: what changes when a server needs a model of its own?

The browsing server of [post 17](../17-research-browser/index.md) turns a page into article text and reports the byte counts that prove the discard. On the article fixture that is 76,201 bytes of markup down to 4,126 bytes and 707 words. That is good, and it is not enough. About seven hundred words per page across five pages is still some three and a half thousand words of reading for a question that might be answered in three sentences.

So the server wants a model of its own: not the host's model answering the user, but a model doing the server's own summarizing work before the result is ever returned.

The previous edition of this post answered that with **sampling**, and the answer was elegant: the server called back through the MCP session, the host's model generated the summary, and nobody needed an application programming interface (API) key. That post opened with the line "here is the mind-bending part: what if the server could ask the model for help?"

That mechanism is gone. The back channel it traveled over does not exist in revision 2026-07-28, and the feature itself is formally deprecated. This is the largest single correction in the series, and the rest of the post is what replaces it.

Vocabulary first, because readers arrive mid-series. The **host** is the application the user talks to, and the thing that owns the model and the conversation. The **client** is the protocol-speaking object inside the host, one per connected server. The **server** is the process this project builds. A **provider** is a model vendor's own Hypertext Transfer Protocol (HTTP) API, reached with a key, and outside MCP entirely.

## 2. What happened to sampling, precisely

Two separate things happened, and conflating them causes confusion later.

**The feature is deprecated.** Specification Enhancement Proposal (SEP) 2577 deprecates roots, sampling, and logging in revision 2026-07-28. The specification's Deprecated features registry gives sampling's migration path in six words, where LLM stands for large language model: "Integrate directly with LLM provider APIs." The earliest removal is the first revision released on or after 2027-07-28, so the types are still in the schema and still work where they can be reached: `ClientCapabilities.sampling`, `ModelPreferences`, `ModelHint`, `ToolChoice`, and `SamplingMessage` all carry `@deprecated` there. The `includeContext` values `"thisServer"` and `"allServers"` were deprecated a revision earlier, in 2025-11-25; `"none"` is the default and the only value worth writing.

**The channel it used was removed, with no deprecation window.** The Patterns overview page says that servers **MUST NOT** initiate JSON-RPC requests (the JavaScript Object Notation remote procedure call protocol MCP is built on), and the Multi Round-Trip Requests (MRTR) page says the previous pattern of server-initiated requests is no longer supported and calls that a breaking change. That is not a deprecation; it is a deletion, and it is the part that breaks code.

`snippets/sampling_today.py` measures both halves against the software development kit (SDK). It builds a throwaway server with two tools that want the host's model, and posts `tools/call` frames by hand to that server's Streamable HTTP application at revision 2026-07-28. The application runs inside the script behind `httpx`'s Asynchronous Server Gateway Interface (ASGI) transport, so no port is opened, and the sampling request is only ever returned, never answered. The first tool is written the pre-2026 way:

```python
@mcp.tool()
async def summarize_old_way(text: str, ctx: Context) -> str:
    """The pre-2026 shape: ask the client's model and wait for the answer."""
    reply = await ctx.session.create_message(
        [SamplingMessage(role="user", content=TextContent(type="text", text=text))],
        max_tokens=800,
    )
    return reply.content.text
```

The SDK marks the call site, and on the 2026-07-28 Streamable HTTP path the per-request context has no channel to send it on:

```
ServerSession.create_message.__deprecated__: 'The sampling capability is deprecated as of 2026-07-28 (SEP-2577).'
2026-07-28 Streamable HTTP, per-request context: can_send_request defaults to False
```

Called with a client that declares the `sampling` capability, the tool issues an `MCPDeprecationWarning` with that same sentence, and then the request fails before anything could reach the client:

```
HTTP 400
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32600,
    "message": "Cannot send 'sampling/createMessage': this transport context has no back-channel for server-initiated requests."
  }
}
```

That message is `NoBackChannelError`, from `mcp.shared.exceptions`, and its code is `-32600` (`Invalid Request`), a code the SDK chose for the case, since the specification's error table names none for it. A deprecation warning in an editor is the polite half of the change; this error is the half that ends a call.

**What survives.** `sampling/createMessage` still exists as a `CreateMessageRequest`, but only inside MRTR, the mechanism [post 08](../08-elicitation-and-mrtr/index.md) teaches. The server does not call the client; it *returns* a request and waits to be called again. The SDK still offers that path through the same `Resolve` pattern post 08 uses for elicitation, with a `Sample` marker in place of `Elicit`. The second tool of the script asks that way:

```python
def ask_host_model(text: str) -> Sample:
    return Sample(
        [SamplingMessage(role="user", content=TextContent(type="text", text=f"Summarize: {text}"))],
        max_tokens=800,
    )


@mcp.tool()
async def summarize_via_mrtr(text: str, summary: Annotated[CreateMessageResult, Resolve(ask_host_model)]) -> str:
    """The 2026-07-28 shape: return the sampling request inside an input_required result."""
    return summary.content.text
```

and the server answers the first `tools/call` with this result, the sealed `requestState` shortened:

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "inputRequests": {
      "__main__:ask_host_model": {
        "method": "sampling/createMessage",
        "params": {
          "maxTokens": 800,
          "messages": [
            {
              "content": {
                "text": "Summarize: ...",
                "type": "text"
              },
              "role": "user"
            }
          ]
        }
      }
    },
    "requestState": "v1.<sealed, shortened here>",
    "resultType": "input_required",
    "_meta": {
      "io.modelcontextprotocol/serverInfo": {
        "name": "sampling-today",
        "version": ""
      }
    }
  }
}
```

The key of the `inputRequests` entry is the resolver's module and name, which the SDK chooses. That is a real path, and it is not the one to build on. It needs the client to declare the deprecated `sampling` capability, it costs a full extra round trip through the host, the host may refuse to run the request, and the whole feature is scheduled for removal. For a server that already ships sampling it is the migration runway; a new server should not adopt it, and this project does not.

**A note on where the sources disagree.** SEP-2577 says that during the deprecation period "wire-level behavior is unchanged" and that no existing implementations break. That is true of the *deprecations*. It is not true of the revision as a whole: the removal of server-initiated requests is a separate change with no window, and it is what breaks a sampling-based server. The same SEP says the deprecations start in a revision "expected June 2026", and it is predicated on a one-year support policy whose adoption the author could not confirm; the registry's dates (deprecated in 2026-07-28, removal no earlier than the first revision released on or after 2027-07-28) are the ones to quote. These readings were taken from the revision's release-candidate pages on 2026-07-26 and re-checked against `mcp==2.3.0` on 2026-10-09, where `create_message` is still present, still marked deprecated, and still refused on the 2026-07-28 Streamable HTTP path, while `Sample` still sends the request through MRTR.

## 3. Who pays, who consented, who answers for the prompt

This is the part that matters more than the mechanism, and the part the previous edition never had to think about, because sampling made all three questions somebody else's problem.

**Who pays for the tokens.** Under sampling, the host did. The user's own subscription or the host operator's API budget absorbed the cost, and the server author never saw an invoice. Calling a provider directly moves that line: the tokens are billed to whoever supplied the key the server holds. A team that deploys this server is buying the summaries. A model call inside a tool has a cost per invocation, and a research pass that visits five pages makes five of them. That cost has to be budgeted explicitly, and section 7 is about why the budget belongs in the code and not in a wiki page.

**Who consented to the prompt.** Under sampling, the specification's Sampling page asked for a human in the loop who could deny a sampling request, so the user stood between the server and the model, at least in principle. Calling a provider directly removes that person. The user asked the host to research a topic; the user did not agree that a second model, at a vendor the user may not have chosen, would receive the text of every page the server fetched. Consent moved from the user at call time to the operator at deploy time, and those are different people making a different decision.

Two consequences follow, and neither is optional. **The server's documentation names the provider**, so an operator knows whose terms apply before setting a key. And **the provider path is opt-in rather than opt-out**, so an operator who never configured a model never silently sends page text anywhere.

**Who answers for the prompt.** This one does not move, which is exactly why it is worth saying. The system prompt, at the top of the provider half of `src/research_browser/summarize.py`, reads:

```python
_SYSTEM_PROMPT = (
    "You summarize web pages for a research assistant. Preserve concrete facts, "
    "numbers, names, and conclusions. Do not add information that is not in the "
    "text. Do not use emoji. Write plain prose, no headings and no bullet lists."
)
```

The server author wrote a prompt under sampling and writes one now. If it instructs a model to editorialize, flatten a caveat, or drop an attribution, the resulting summary carries the server author's judgment into the user's answer under the host's branding. Sampling never insulated the author from that, and the direct call does not create it. What the direct call changes is that the prompt no longer passes through the host, so nobody downstream can review it. It belongs in a constant, in version control, and a change to it is a change to behavior.

## 4. A protocol, and a default that needs no model

[summarize.py](../../code/17-research-browser/src/research_browser/summarize.py) is built around a two-member protocol, the entire seam between "this server summarizes" and "this server has an opinion about which vendor the operator pays":

```python
class Summarizer(Protocol):
    """Anything that can shorten text.

    Two members. That is the entire seam between "this server summarizes" and
    "this server has an opinion about which vendor you pay".
    """

    name: str

    async def summarize(
        self,
        text: str,
        *,
        focus: str | None = None,
        max_sentences: int = DEFAULT_MAX_SENTENCES,
    ) -> str: ...
```

The default implementation needs no network, no key, and no budget. `ExtractiveSummarizer` ranks the document's own sentences by term frequency and returns the best few, verbatim. The scoring is a few lines of the same file:

```python
        peak = max(frequencies.values())
        focus_terms = set(_content_words(focus)) if focus else set()

        scored: list[tuple[float, int]] = []
        for index, sentence in enumerate(sentences):
            words = _content_words(sentence)
            if not words:
                continue
            # Normalizing by sqrt(length) stops a single long paragraph-sentence
            # from winning on sheer word count alone.
            score = sum(frequencies[w] / peak for w in words) / math.sqrt(len(words))
            if focus_terms:
                overlap = len(focus_terms.intersection(words))
                score *= 1.0 + 0.5 * overlap
            scored.append((score, index))
```

`snippets/summarizers.py` runs it on the six-sentence text the project's tests use and asks for three:

```
Extraction removes the boilerplate from a web page.
Boilerplate removal and summarization solve different halves of the same problem.
A cache avoids paying for extraction twice in one session.
identical on a second run: True
every sentence is in the source: True
in document order: True
```

It is worse than a model, and it has four properties a generated summary does not.

**It is always available.** A server whose only summarization path needs a credential does nothing on a machine that has never seen one. The test `test_no_key_means_no_network` exists for that sentence: `default_summarizer(env={})` returns the extractive one.

**It is deterministic.** Ties resolve by document position, so identical input gives identical output. A summarizer that returns different text for the same page cannot be tested and cannot be cached.

**It preserves document order.** The chosen sentences come back in the order they appeared, so the summary reads as an abridgement rather than a shuffle.

**Every sentence provably appeared on the page.** Section 8 depends on this one. An extractive claim can be checked against the source text; a generated one cannot.

One detail at the end of the method is load-bearing and easy to get wrong. Sentences are joined with a newline, not a space:

```python
        # One sentence per line, not space-joined. Headings and list items have
        # no terminating punctuation, so a space-joined summary cannot be split
        # back into the sentences it was built from -- and post 18 needs exactly
        # that to attribute each claim to the page it came from.
        return "\n".join(sentences[i] for i in chosen)
```

The same script takes a heading and the sentence after it from the article fixture and splits them again both ways:

```
two consecutive units of the article fixture: ['Where the bytes actually go', 'Take any article page apart and the same four categories account for nearly all of its weight.']
space-joined, split again:   ['Where the bytes actually go Take any article page apart and the same four categories account for nearly all of its weight.']
newline-joined, split again: ['Where the bytes actually go', 'Take any article page apart and the same four categories account for nearly all of its weight.']
```

Space-joined, the heading and the sentence become one unsplittable string, and the citation step that needs to attribute each sentence on its own has nothing to work with.

## 5. The optional provider path

`AnthropicSummarizer` is the opt-in half. Nothing in it is reachable unless the operator installed the extra and set a key, which is the point. Its request, in the same file:

```python
        message = await self._require_client().messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_SYSTEM_PROMPT,
            # `effort` is where thinking depth is controlled now; a summary does
            # not need deep reasoning, and max_tokens covers thinking plus text.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"{instruction}\n\n{text}"}],
        )
```

Three details in that call belong to the provider's current API, as the Anthropic API documentation describes it for the project's default model, `claude-opus-5`, and are worth knowing rather than copying blind. `effort` lives inside `output_config`, not at the top level, and it is where thinking depth is controlled; a summary does not need deep reasoning. Thinking is on by default on that model and `max_tokens` covers thinking plus visible text, so it needs headroom rather than being sized tight around the answer (the project's default is 4,096). And there is no `temperature` and no `top_p`, because the sampling parameters were removed on that model and sending one is a `400`. The test `test_the_provider_prompt_carries_the_focus_and_the_budget` asserts their absence from the request, which is cheaper than rediscovering it in production. `snippets/summarizers.py` drives the adapter with a stub client that records what it is sent:

```
request keys: ['max_tokens', 'messages', 'model', 'output_config', 'system']
output_config: {'effort': 'low'}, max_tokens: 4096
temperature sent: False, top_p sent: False
```

Reading the reply has its own trap:

```python
        parts = [
            block.text
            for block in message.content
            if getattr(block, "type", None) == "text"
        ]
```

`content` is a list of blocks, and the first one is not guaranteed to be text: with thinking on, a `thinking` block can come first. The script hands the adapter exactly that, and the filter picks the text:

```
content types: ['thinking', 'text'] -> summary 'the summary'
```

Selection is by environment, and the order is deliberate:

```python
    forced = (env.get(FORCE_ENV) or "").strip().lower()
    if forced == "extractive":
        return ExtractiveSummarizer()

    api_key = (env.get(API_KEY_ENV) or "").strip()
    if not api_key and forced != "anthropic":
        return ExtractiveSummarizer()
```

An explicit `RESEARCH_BROWSER_SUMMARIZER=extractive` beats a configured key, so a deployment can turn the provider off without unsetting credentials the rest of the machine needs. A key with the `anthropic` package missing logs a warning and degrades rather than taking the server down over an optional dependency. The script builds the environments as dictionaries, so no real environment is read:

```
anthropic package installed: False
no variables at all                        -> extractive
a key, and the override set to extractive  -> extractive
a key, package missing                     -> extractive
                                              logged: WARNING research-browser.summarize: ANTHROPIC_API_KEY is set but the anthropic package is not installed; using the extractive summarizer
```

The project's test suite pins the extractive summarizer for every test through the autouse fixture `isolated_server` in `tests/conftest.py`. That is not tidiness. Without it, a developer who happens to have a key exported runs a different code path than continuous integration does, and possibly a billable one.

## 6. Never label a fallback as the model

The first edition of this post shipped a defect that deserves more attention than any of the protocol changes, because it is the kind that survives review.

Its summarizer wrapped the sampling call in `try`/`except`. On failure it fell through to truncating the text at two thousand words. Then the calling tool formatted the result and appended a footer:

```
---
*Summarized via MCP Sampling. Use `browse_url` for full content.*
```

The footer was printed on the failure path too. A caller receiving that text had a summary labeled as the product of a model, when it was in fact the first two thousand words of the page with the rest thrown away.

That is not a cosmetic bug. Consider what a reader does with each answer. Given a model summary, the reader weighs it as a compression of the whole page, so a fact absent from it is a fact the model judged unimportant. Given a truncation, the reader knows the tail is simply missing. The label decides which of those two readings is correct, and a wrong label makes the correct reading unavailable. Worse, the failure is silent and repeatable: an expired key produces plausible output forever, and nothing in the result says so.

So `Summary` records what actually ran, next to what was asked for:

```python
@dataclass
class Summary:
    """A summary and its provenance.

    `summarizer` is what actually ran. `requested` is what was asked for. When
    they differ, `fell_back` is true and `note` says why. A caller can never be
    told a model wrote this when it did not.
    """

    text: str
    summarizer: str
    requested: str
    fell_back: bool
    note: str
    input_words: int
    summary_words: int
    reduction_percent: float
```

and the wrapper `summarize()` fills it honestly:

```python
    try:
        summary_text = await summarizer.summarize(
            text, focus=focus, max_sentences=max_sentences
        )
        ran = requested
    except Exception as exc:
        fell_back = True
        # str(exc) can be long and can echo the request; one line is enough to
        # diagnose and short enough not to blow up the tool result.
        note = f"{requested} failed ({type(exc).__name__}: {exc}); used extractive"
        log.warning("summarizer %s failed: %s", requested, exc)
        fallback = ExtractiveSummarizer()
        summary_text = await fallback.summarize(
            text, focus=focus, max_sentences=max_sentences
        )
        ran = fallback.name
```

`snippets/summarizers.py` drives that path with a provider adapter whose stub client raises a `RuntimeError` standing in for an expired key:

```json
{
  "text": "Extraction removes the boilerplate from a web page.\nBoilerplate removal and summarization solve different halves of the same problem.\nA cache avoids paying for extraction twice in one session.",
  "summarizer": "extractive",
  "requested": "anthropic:claude-opus-5",
  "fell_back": true,
  "note": "anthropic:claude-opus-5 failed (RuntimeError: 401 invalid x-api-key); used extractive",
  "input_words": 63,
  "summary_words": 29,
  "reduction_percent": 54.0
}
```

Everything a caller needs is in there: which summarizer ran, which one was wanted, that a fallback happened, and why. With a stub that answers, the same script prints the successful path, the same shape with the two names equal and the note empty:

```
summarizer='anthropic:claude-opus-5' requested='anthropic:claude-opus-5' fell_back=False note=''
```

The tool `summarize_page` carries those fields into its published output schema. `snippets/research_runs.py` calls it over an in-memory client on the article fixture, asking for three sentences, and prints the result with the summary text elided:

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

Seven hundred and seven words to fifty, with the summarizer named in the result rather than in the prose. The rule generalizes past summarization. **If a tool has more than one way to produce its answer, the result must say which one produced it**, and the field must be part of the published output schema rather than a sentence in a text block.

## 7. Multi-page research: the loop and the budget

![A call to research_urls removes duplicate URLs, keeping the caller's order, then enters a loop drawn as a dashed container. Each pass starts at a budget gate comparing pages visited against max_pages and words read against max_words. Inside the loop a page is loaded, summarized with whichever summarizer actually ran recorded, and stored with its citations. Below the loop, five labeled outcomes lead into one ResearchReport: the page limit, the word limit, a failure written to the failures list while the pass keeps going, completion, and no readable source at all.](diagrams/02-research-loop.svg)

*Every exit from the loop is named in the result, so "it finished" and "it ran out" are told apart.*

One page at a time is a browsing server. Several pages, a budget, and an audit trail make a research server, and [research.py](../../code/17-research-browser/src/research_browser/research.py) is the difference. This post adds two tools to the five of [post 17](../17-research-browser/index.md), and `snippets/research_runs.py` prints what they publish:

```
summarize_page  inputs=['url', 'focus', 'max_sentences']
                outputSchema=yes  readOnlyHint=True
research_urls   inputs=['topic', 'urls', 'focus', 'max_pages', 'max_words']
                outputSchema=yes  readOnlyHint=True
```

**A budget is not optional when a model is involved.** An unbounded loop over a list of model-supplied URLs is a way to spend an afternoon, a context window, and somebody's API balance. Three ceilings, in one dataclass:

```python
@dataclass
class Budget:
    """What a single research pass is allowed to spend."""

    max_pages: int = DEFAULT_MAX_PAGES
    max_words: int = DEFAULT_MAX_WORDS
    max_claims_per_page: int = DEFAULT_CLAIMS_PER_PAGE
```

The defaults are five pages, 20,000 words, and three claims per page. The gate runs at the top of each iteration of `run_research`, and when it trips it says exactly what tripped it:

```python
        if len(sources) >= budget.max_pages:
            stopped_because = (
                f"budget: page limit reached ({budget.max_pages}), "
                f"{len(ordered) - index} source(s) not visited"
            )
            break
```

The script runs `research_urls` over the same three fixture URLs with different budgets:

```
{"max_pages": 2}   pages_visited=2 words_read=841  stopped_because: budget: page limit reached (2), 1 source(s) not visited
{"max_words": 500} pages_visited=1 words_read=707  stopped_because: budget: word limit reached (500), 2 source(s) not visited
{"max_words": 100} pages_visited=1 words_read=707  stopped_because: budget: word limit reached (500), 2 source(s) not visited
(defaults)         pages_visited=3 words_read=1106  stopped_because: completed: visited every source
```

Set the first two lines against a report that simply contains two sources, or one. The pages are the same, and the facts are different. "Every source was read" and "the budget ran out and some were skipped" call for different next actions from the caller, and a report that cannot tell them apart forces the caller to guess. (The third line is a trap section 11 explains.)

**A dead link does not end the pass.** One unreachable URL in a list of five is a normal day:

```python
        try:
            extraction, from_cache = await load_page(url)
        except Exception as exc:
            # One dead link must not end the pass. Record it and move on: a
            # report that says which sources failed is more useful than one
            # that quietly has fewer sources than you asked for.
            failures.append(f"{url}: {exc}")
            log.warning("research: skipping %s (%s)", url, exc)
            continue
```

The failure is recorded and named, and the pass continues.

**Duplicates are removed, order is kept.** A duplicate URL is a wasted page against the budget and a duplicated bill. The loop deduplicates, but keeps the caller's order, because the caller's order may say which source the caller trusts most.

Two smaller decisions round it out. The loop lives inside a single `tools/call` rather than using the tasks extension of [post 09](../09-tasks/index.md), which is the right choice for five pages and the wrong one for five hundred; a pass that will outlive a request belongs in a task. And every page goes through the same `load_page` as the browsing tools of post 17, so the cache is shared: a URL already browsed in this session costs no second navigation and no second extraction.

## 8. Every claim carries its URL

![Three panels. The first is a page as the extractor left it, with its URL, its title, and one sentence outlined to mark the sentence the summarizer chose. The second is the citation log, whose add method checks that the claim is not empty, that the URL is not empty, and whether the claim appears verbatim in the source text; a branch below shows an empty URL raising MissingSourceError so that nothing is appended. The third is the resulting citation in the report, carrying claim, URL, title, and verbatim flag. A return path shows any claim being walked back to the sentence it came from.](diagrams/03-citation-provenance.svg)

*Provenance enforced by the type rather than promised in a docstring.*

A research report whose claims cannot be traced is a rumor with formatting. The usual way to handle that is a rule in the prompt: "include citations for each source." That is a request, and the model may decline it.

This project makes it a property of the code instead. There is exactly one way a claim enters a report, `CitationLog.add`, and it refuses:

```python
    def add(self, claim: str, *, url: str, title: str, source_text: str = "") -> Citation:
        claim = claim.strip()
        if not claim:
            raise MissingSourceError("a claim cannot be empty")
        if not url.strip():
            raise MissingSourceError(
                f"refusing to record a claim with no source URL: {claim[:60]!r}"
            )
        citation = Citation(
            claim=claim,
            url=url.strip(),
            title=title,
            # Verifiable rather than asserted: does this sentence actually
            # appear in the text we extracted from that page?
            verbatim=bool(source_text) and claim in source_text,
        )
```

It raises rather than logging and skipping. An unsourced claim is never recorded and then filtered out later, because there is nothing to filter. The report is assembled only from `citations.entries`, so no code path exists by which an unattributed claim reaches the caller.

The `verbatim` flag is checked, not asserted. The extractive summarizer returns sentences that appeared on the page, so its claims come back `verbatim: true` and can be quoted. A model's paraphrase is still cited, and is recorded with `verbatim: false`. Both are attributable; only one can go inside quotation marks. Tests pin each half, including the case where the claim really is not in the source.

`snippets/research_runs.py` runs `research_urls` over the two article fixtures. This is the report with `sources` and `citations` elided:

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

and this is the second of the three claims taken from the second page:

```json
{
  "claim": "The regulator has opened a twelve-week consultation on how data portability obligations should apply to platforms above the designated user threshold.",
  "url": "https://example.com/news/portability-consultation",
  "title": "Regulator opens consultation on data portability rules",
  "verbatim": true
}
```

The first claim from that page is its heading, "Regulator opens consultation on data portability rules", which is the newline join of section 4 doing its job: a unit without a full stop is still one attributable claim. The script also prints the per-source records:

```
citations: 6, per page: [3, 3], all verbatim: True
source https://example.com/blog/what-a-page-costs: raw_bytes=76,201 clean_bytes=4,126 words=707 summarizer=extractive
source https://example.com/news/portability-consultation: raw_bytes=243,128 clean_bytes=860 words=134 summarizer=extractive
raw bytes, both pages: 319,329
```

Stack the two reductions and the shape of the whole project appears: 319,329 bytes of markup became 841 words of article, and 841 words of article became 113 words of claims, every one of them carrying the page it came from.

The same script then puts a provider summarizer on a stub client in place of the extractive one, and runs the article alone. The label in the report changes, and the paraphrase is cited but not marked verbatim:

```
summarizer: anthropic:claude-opus-5, source summarizer: anthropic:claude-opus-5
{
  "claim": "Most of a page's bytes are markup the reader never sees.",
  "url": "https://example.com/blog/what-a-page-costs",
  "title": "What a web page actually costs a language model",
  "verbatim": false
}
```

The stub's sentence is the script's own, not a model's; it stands in for a provider so that the label and the flag can be checked without a key.

## 9. When not to call a model from a server

Three cases where the honest answer is that the server should not have a model at all.

**When the host's model can do it in the same turn.** If a tool returns two thousand words and the host's model is about to read them anyway, summarizing them first spends money to discard information the host has already paid to receive. Summarizing pays when the server visits many pages and most of them will not matter, not when it returns one page to a model that asked for it.

**When the provider cannot be disclosed.** If a deployment cannot tell its users that page text leaves for a third party, it should not make the call. It can ship the extractive path and say so. A worse summary that everyone understands beats a better one nobody agreed to.

**When determinism matters more than quality.** A generated summary varies between runs, which makes downstream caching, diffing, and regression testing harder. The extractive path is reproducible, and for a pipeline compared against yesterday's output that property can outweigh the prose.

## 10. Make it run: the project and the scripts behind this post

The project is the post's code, and its suite needs no network, no key, and no browser binary. From inside `code/17-research-browser/`:

```bash
uv sync --extra dev                  # once: the environment, with the test tools
uv run --extra dev pytest            # 75 passed, 1 skipped
uv run playwright install chromium   # once, for live pages; the suite does not need it
uv run mcp-research-browser          # stdio, what a desktop host spawns
```

```
tests\test_cache.py ..........                                           [ 13%]
tests\test_extract.py .............                                      [ 30%]
tests\test_server.py ...................................                 [ 76%]
tests\test_summarize.py ................s.                               [100%]

======================== 75 passed, 1 skipped in 1.68s ========================
```

The count is the result; the seconds vary by machine. The single skip is `test_a_key_selects_the_provider_when_the_sdk_is_installed`, which uses `pytest.importorskip` and reports "the provider extra is optional and not installed here". That is the intended state, and its sibling test checks the other half: a key set with the package missing degrades to the extractive summarizer rather than crashing.

The provider is opt-in at install time as well as at configuration time. `pyproject.toml` declares it as an extra:

```toml
provider = [
    "anthropic>=0.60",
]
```

```bash
uv sync --extra provider                      # the provider package, still inactive without a key
export ANTHROPIC_API_KEY=...                  # now the provider path activates
export RESEARCH_BROWSER_SUMMARIZER=extractive # and this turns it off again, key or no key
```

Three scripts under `snippets/` produce every frame, result, and log line quoted above. Each runs from the series root with no arguments in a few seconds, imports the project from `code/17-research-browser/src` (and, where it needs pages, the `FixtureFetcher` of `tests/conftest.py`), and needs no host, no browser, and no provider. The root environment pins `mcp[cli]==2.3.0` and the project's libraries.

```bash
uv run python posts/18-server-side-models/snippets/sampling_today.py   # sections 2 and 11: the old call, the MRTR request, -32021
uv run python posts/18-server-side-models/snippets/summarizers.py      # sections 4 to 6: selection, the extractive default, the adapter on stubs
uv run python posts/18-server-side-models/snippets/research_runs.py    # sections 6 to 8 and 11: the tools, budgets, citations, a dead link
```

To see the label change against a real model, install the extra, export a key, connect a host to the server, and call `summarize_page` or `research_urls`: `summarizer` then reads `anthropic:claude-opus-5` (or the model named in `RESEARCH_BROWSER_MODEL`), and a paraphrased citation comes back `verbatim: false`. That run sends page text to the provider and is billed to the key's owner, which is why nothing in this post makes it.

## 11. What can go wrong?

Four failures do not announce themselves, and the scripts above print each one.

**A key, but no package.** An operator exports `ANTHROPIC_API_KEY` and expects summaries from a model, but never ran `uv sync --extra provider`. The server starts, every result says `"summarizer": "extractive"`, and the only other trace is one line on standard error, printed in section 5: `ANTHROPIC_API_KEY is set but the anthropic package is not installed; using the extractive summarizer`. The `summarizer` field is the thing to check, which is exactly why it exists.

**A client that does not declare sampling, on the MRTR path.** A server that keeps sampling during its migration, as `summarize_via_mrtr` does, meets clients that no longer declare the deprecated capability. `snippets/sampling_today.py` sends the same call with empty client capabilities:

```
HTTP 400
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32021,
    "message": "Client did not declare the sampling capability required by resolver '__main__:ask_host_model'",
    "data": {
      "requiredCapabilities": {
        "sampling": {}
      }
    }
  }
}
```

The tool fails outright, so a server still on the runway needs a path that works without sampling, which is the case for the direct call all over again.

**"Completed" with a page missing.** A dead link first, then a page that loads, then the same page again:

```
pages_visited=1 pages_failed=1
failures: ['https://example.com/gone: could not load https://example.com/gone: net::ERR_NAME_NOT_RESOLVED']
stopped_because: completed: visited every source
```

The `net::ERR_NAME_NOT_RESOLVED` text is the suite's fixture fetcher imitating what a real browser reports for a host that does not resolve; a live run would carry the browser's own message.

`stopped_because` describes the loop, not the outcome: every source was *attempted*, and one of them failed. A caller that reads only `stopped_because` misses the failure; `pages_failed` and `failures` carry it. (The URL appears twice because `load_page` already names it in its `ToolError` and the loop prefixes it again.)

**A word budget that is not the one asked for.** The third budget line in section 7 asked for 100 words and was stopped at 500, because `research_urls` clamps `max_words` to at least 500 and `max_pages` to between 1 and 20. The gate also runs *before* each page, so a pass can overshoot by up to one page: the 500-word budget read 707 words. The word limit is a stopping rule, not a cap on what one page can cost.

## Common pitfalls

1. **Reaching for `ctx.session.create_message()` and trusting the deprecation timeline.** The feature has a twelve-month window, but the channel it used was deleted outright; on the 2026-07-28 path the call raises `NoBackChannelError` (`-32600`), so the window protects the types, not the call site.
2. **Labeling a fallback as the thing that failed.** Catching a provider exception, returning other text, and keeping the "summarized by the model" wording is the defect this module exists not to have; record what ran, what was requested, and why they differ, in the structured result.
3. **Making the provider the only path.** A server whose summarization needs a credential does nothing on a machine without one, and its suite cannot run in continuous integration; build the offline default first and make the provider an extra.
4. **Space-joining extracted sentences.** Headings and list items carry no terminating punctuation, so a space-joined summary cannot be split back into the units it was built from, and per-claim citation becomes impossible; join with a newline.
5. **Asking for citations in the prompt instead of enforcing them in the type.** A prompt rule is a request the model may decline; make the log refuse an unsourced claim, and assemble the report only from the log.
6. **Running a research loop with no ceiling and no stop reason.** Pages and words need limits, and the report has to say which one stopped the pass, because "it finished" and "it ran out of budget" otherwise produce identically shaped output.

## Further reading

- Specification, *"Deprecated features"*, revision 2026-07-28. The registry entry for sampling, its SEP-2577 deprecation, the migration path, and the earliest removal date. <https://modelcontextprotocol.io/specification/2026-07-28/deprecated>
- Specification, *"Sampling"*, revision 2026-07-28. Still present, still documented, and marked deprecated. <https://modelcontextprotocol.io/specification/2026-07-28/client/sampling>
- Specification, *"Multi Round-Trip Requests (MRTR)"* and the Patterns overview, revision 2026-07-28. The end of server-initiated requests, and the `inputRequests` map in which `sampling/createMessage` still travels. <https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/mrtr>
- SEP-2577, *"Deprecate roots, sampling, and logging"* (2026). Its own caveat that wire-level behavior is unchanged during the deprecation period, read against the separate removal of server-initiated requests. <https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2577>
- Anthropic API documentation. Model identifiers, `output_config.effort`, and the removal of the sampling parameters on the current models. <https://platform.claude.com/docs>
- MCP Python SDK, release 2.3.0. `NoBackChannelError`, the deprecation marker on `ServerSession.create_message`, and the `Resolve` and `Sample` pair the snippets use. <https://github.com/modelcontextprotocol/python-sdk>
- [code/17-research-browser/](../../code/17-research-browser/). The complete project, with the tests run in section 10.

Full citations are in [REFERENCES.md](../../REFERENCES.md).

## What to read next

- **[Post 19 - Security: the attacks the protocol does not stop](../19-security/index.md):** this server fetches any URL a model hands it and pipes the result into a second model's prompt, which is two attack surfaces in one tool.
- **[Post 08 - Elicitation and MRTR: asking the user mid-call](../08-elicitation-and-mrtr/index.md):** the mechanism that replaced the server-to-client channel, and the only place a `CreateMessageRequest` can still travel.
