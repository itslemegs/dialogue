from __future__ import annotations

import anyio
import json
import os
import re
import asyncio
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.local_ollama import get_chat_client
from app.services.web_search import WebSearchClient, SearchResult
from app.services.web_fetch import fetch_page_text, FetchedPage


# -----------------------------
# Models
# -----------------------------

class LookupRequest(BaseModel):
    text: str
    jurisdiction: str = ""          # e.g., "Japan", "EU", "Indonesia", "UN"
    topic_hint: str = ""            # e.g., "privacy law", "human rights", "trade"
    freshness: str = ""             # optional provider hint (Brave: "pw", "pm", etc.)
    max_items: int = int(os.getenv("LOOKUP_MAX_ITEMS", "6"))
    max_queries_per_item: int = int(os.getenv("LOOKUP_MAX_QUERIES_PER_ITEM", "3"))
    results_per_query: int = int(os.getenv("LOOKUP_RESULTS_PER_QUERY", "5"))
    fetch_top_k: int = int(os.getenv("LOOKUP_FETCH_TOP_K", "3"))


class LookupPlanItem(BaseModel):
    claim: str
    why_check: str = ""
    queries: List[str] = Field(default_factory=list)
    preferred_domains: List[str] = Field(default_factory=list)  # optional


class LookupPlan(BaseModel):
    items: List[LookupPlanItem] = Field(default_factory=list)


class Source(BaseModel):
    id: str
    title: str = ""
    url: str
    snippet: str = ""
    display_url: str = ""
    provider: str = ""
    rank: int = 0
    fetched_title: str = ""
    fetched_text_excerpt: str = ""   # short excerpt only
    fetched_truncated: bool = False


class Finding(BaseModel):
    what_it_says: str = ""          # neutral, no legal advice
    relevant_to: str = ""           # why it matters for the claim
    source_ids: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)


class LookupItemReport(BaseModel):
    claim: str
    why_check: str = ""
    findings: List[Finding] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)

class LookupReport(BaseModel):
    disclaimer: str
    items: List[LookupItemReport] = Field(default_factory=list)
    debug: Dict[str, Any] = Field(default_factory=dict)  # <--- add


# -----------------------------
# Robust JSON helpers
# -----------------------------

SMART_QUOTES = {
    "\u201c": '"', "\u201d": '"',
    "\u2018": "'", "\u2019": "'",
}

def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)

def _extract_first_json(text: str) -> str:
    t = _strip_code_fences(text)
    # try object
    m = re.search(r"\{.*\}", t, flags=re.S)
    if m:
        return m.group(0)
    # try array
    m = re.search(r"\[.*\]", t, flags=re.S)
    if m:
        return m.group(0)
    return t

def _repair_json(text: str) -> str:
    t = text
    for k, v in SMART_QUOTES.items():
        t = t.replace(k, v)
    # remove trailing commas
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t

def _parse_json(text: str) -> Any:
    raw = _repair_json(_extract_first_json(text))
    return json.loads(raw)

def _heuristic_plan(req: LookupRequest) -> LookupPlan:
    """
    Fast plan that avoids an LLM call for planning and never fails JSON parsing.
    Great for testing + faster overall.
    """
    text = (req.text or "").strip()
    first_line = (text.splitlines()[0] if text else "").strip()
    claim = (first_line[:220] if first_line else text[:220]).strip() or "Draft claim"

    # Simple query: claim + jurisdiction + topic hint
    q = " ".join(x for x in [claim, req.jurisdiction, req.topic_hint] if x).strip()
    if not q:
        q = text[:200]

    return LookupPlan(items=[
        LookupPlanItem(
            claim=claim,
            why_check="Fast mode heuristic (plan JSON failed or disabled).",
            queries=[q],
            preferred_domains=[],
        )
    ])

import re

def _fallback_findings_from_sources(sources: List[Source], max_n: int = 3) -> List[Finding]:
    out: List[Finding] = []
    for s in sources[:max_n]:
        if (s.snippet or "").strip():
            out.append(
                Finding(
                    what_it_says=s.snippet.strip(),
                    relevant_to="",
                    source_ids=[s.id],
                    open_questions=[],
                )
            )
    if not out:
        out.append(
            Finding(
                what_it_says="No sources returned for this query.",
                relevant_to="",
                source_ids=[],
                open_questions=[],
            )
        )
    return out

def _parse_bullets_with_citations(text: str) -> List[Finding]:
    """
    Expected format:
      - summary text [s1][s3]
      - another point [s2]
    """
    t = _strip_code_fences(text).strip()
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]

    findings: List[Finding] = []
    for ln in lines:
        if not (ln.startswith("-") or ln.startswith("*") or ln.startswith("•")):
            continue

        body = ln.lstrip("-*•").strip()

        # citations in [s1] form preferred
        cids = re.findall(r"\[(s\d+)\]", body)
        if not cids:
            # tolerate "s1, s2" if model disobeys
            cids = re.findall(r"\b(s\d+)\b", body)

        # remove citations from summary text
        summary = re.sub(r"\[(s\d+)\]", "", body)
        summary = re.sub(r"\b(s\d+)\b", "", summary)
        summary = re.sub(r"\s{2,}", " ", summary).strip(" \t-–—,:;.")

        if summary:
            findings.append(
                Finding(
                    what_it_says=summary,
                    relevant_to="",
                    source_ids=list(dict.fromkeys(cids))[:4],
                    open_questions=[],
                )
            )

    return findings

# -----------------------------
# LLM calls
# -----------------------------

import anyio
from typing import List, Dict

async def _llm_call(messages: List[Dict[str, str]], *, temperature: float = 0.2, json_mode: bool = False) -> str:
    client = get_chat_client()
    max_tokens = int(os.getenv("LOOKUP_LLM_MAX_TOKENS", "700"))
    timeout_s = float(os.getenv("OLLAMA_TIMEOUT_S", "300"))

    def _call():
        # Works whether or not you added json_mode support to LocalChatClient
        try:
            return client.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                json_mode=json_mode,
            )
        except TypeError:
            return client.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )

    resp = await anyio.to_thread.run_sync(_call)
    return (resp.choices[0].message.content or "").strip()


async def build_lookup_plan(req: LookupRequest) -> LookupPlan:
    # Auto-fast mode: if you're testing and want speed
    auto_fast = (
        req.max_items <= 1
        and req.max_queries_per_item <= 1
        and req.fetch_top_k <= 0
        and os.getenv("LOOKUP_AUTO_FAST", "1") == "1"
    )
    if auto_fast:
        return _heuristic_plan(req)

    sys = (
        "You are a legal research assistant. "
        "Identify specific claims to cross-check and propose web queries. "
        "Output ONLY valid JSON."
    )
    user = (
        "Return JSON as:\n"
        "{\n"
        '  "items": [\n'
        '    {"claim": "...", "why_check": "...", "queries": ["..."], "preferred_domains": ["..."]}\n'
        "  ]\n"
        "}\n\n"
        f"Jurisdiction: {req.jurisdiction or 'unspecified'}\n"
        f"Topic hint: {req.topic_hint or 'unspecified'}\n"
        f"Max items: {req.max_items}\n"
        f"Max queries per item: {req.max_queries_per_item}\n\n"
        "Draft:\n"
        f"{req.text}\n"
    )

    out = await _llm_json(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.1,
    )

    try:
        data = _parse_json(out)
        plan = LookupPlan.model_validate(data)
    except Exception as e:
        # Don’t crash the endpoint — fallback.
        print(f"[lookup] plan JSON parse failed: {type(e).__name__}: {e}")
        print(f"[lookup] raw plan output (trunc): {out[:800]}")
        plan = _heuristic_plan(req)

    # hard caps
    plan.items = plan.items[: req.max_items]
    for it in plan.items:
        it.queries = [q.strip() for q in it.queries if q and q.strip()][: req.max_queries_per_item]
        it.preferred_domains = [d.strip() for d in it.preferred_domains if d and d.strip()][:5]
    return plan


def _dedupe_results(results: List[SearchResult]) -> List[SearchResult]:
    seen = set()
    out = []
    for r in results:
        u = r.url.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def _make_site_restricted_query(query: str, domains: List[str]) -> str:
    # domains like: ["un.org", "oecd.org", "gov.jp"]
    if not domains:
        return query
    # naive: OR chain of site:
    sites = " OR ".join([f"site:{d}" for d in domains])
    return f"({query}) ({sites})"


async def _search_and_fetch(
    search: WebSearchClient,
    query: str,
    *,
    results_per_query: int,
    fetch_top_k: int,
    freshness: str = "",      # keep param so callers don't change
    language: str = "",       # optional: pass req.jurisdiction language later
) -> List[Source]:
    """
    SearxNG client signature: search(query, count=..., pageno=..., time_range=..., language=...)
    - We map freshness -> time_range if it's one of: day|month|year
    - We do not use offset; SearxNG paginates by page number.
    """
    # Map older "freshness" to SearxNG time_range (best-effort)
    time_range = None
    if freshness in ("day", "month", "year"):
        time_range = freshness

    try:
        hits = await search.search(
            query,
            count=results_per_query,
            pageno=1,
            time_range=time_range,
            language=(language or None),
        )
    except Exception as e:
        print(f"[lookup] search error for query={query!r}: {e}")
        hits = []
    hits = _dedupe_results(hits)

    # # fetch top K pages in parallel
    # top = hits[: max(fetch_top_k, 0)]
    # fetched: List[FetchedPage] = []
    # if top:
    #     fetched = await asyncio.gather(*[fetch_page_text(h.url) for h in top], return_exceptions=False)

    # fetch top K pages in parallel (ONLY if enabled)
    fetched: List[FetchedPage] = []
    top = hits[: max(fetch_top_k, 0)]
    if fetch_top_k > 0 and top:
        fetched = await asyncio.gather(*[fetch_page_text(h.url) for h in top], return_exceptions=False)

    sources: List[Source] = []
    for i, h in enumerate(hits):
        sid = f"s{i+1}"
        fp: Optional[FetchedPage] = None
        if i < len(fetched):
            fp = fetched[i]

        excerpt = ""
        trunc = False
        ftitle = ""
        if fp and fp.text:
            excerpt = fp.text[:1500]
            trunc = fp.truncated
            ftitle = fp.title

        sources.append(
            Source(
                id=sid,
                title=h.title,
                url=h.url,
                snippet=h.snippet,
                display_url=h.url,       # searxng result doesn't always have display_url
                provider=h.provider,
                rank=h.rank,
                fetched_title=ftitle,
                fetched_text_excerpt=excerpt,
                fetched_truncated=trunc,
            )
        )
    return sources


async def _llm_write_report(req: LookupRequest, plan_item: LookupPlanItem, sources: List[Source]) -> LookupItemReport:
    # Keep the model context small: id + title + url + snippet only
    src_payload = []
    for s in sources[:8]:
        src_payload.append(
            {
                "id": s.id,
                "title": s.title or s.fetched_title or "",
                "url": s.url,
                "snippet": (s.snippet or "")[:240],
            }
        )

    sys = (
        "You are a legal research assistant (paralegal). "
        "Write ONLY short bullet points summarizing what the SOURCES suggest. "
        "DO NOT give legal advice. DO NOT recommend actions.\n\n"
        "FORMAT RULES (MANDATORY):\n"
        "1) Output ONLY bullet lines starting with '-'. No headings.\n"
        "2) Each bullet MUST end with citations like [s1][s3].\n"
        "3) 1 to 3 bullets total. Each bullet <= 2 sentences.\n"
        "4) Only cite source ids that exist.\n"
    )

    user = (
        f"Claim to cross-check:\n{plan_item.claim}\n\n"
        f"Jurisdiction: {req.jurisdiction or 'unspecified'}\n"
        f"Topic hint: {req.topic_hint or 'unspecified'}\n\n"
        f"SOURCES:\n{json.dumps(src_payload, ensure_ascii=False)}\n\n"
        "Write the bullets now."
    )

    # plain text output (no JSON)
    out = await _llm_call(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.2,
        json_mode=False,
    )

    findings = _parse_bullets_with_citations(out)

    # If model output is unusable, fall back to snippets (never crash)
    if not findings:
        findings = _fallback_findings_from_sources(sources, max_n=3)

    return LookupItemReport(
        claim=plan_item.claim,
        why_check=plan_item.why_check,
        findings=findings[:3],
        sources=sources,  # keep for UI citation links
    )


# -----------------------------
# Public entry point
# -----------------------------

async def run_lookup(req: LookupRequest) -> LookupReport:
    plan = await build_lookup_plan(req)
    search = WebSearchClient()

    items_out: List[LookupItemReport] = []
    for it in plan.items:
        all_sources: List[Source] = []

        for q in it.queries:
            qq = _make_site_restricted_query(q, it.preferred_domains)
            srcs = await _search_and_fetch(
                search,
                qq,
                results_per_query=req.results_per_query,
                fetch_top_k=req.fetch_top_k,
                freshness=req.freshness,
            )
            all_sources.extend(srcs)

        # de-dupe sources by URL, keep best ranks first
        by_url = {}
        for s in all_sources:
            if s.url not in by_url:
                by_url[s.url] = s
        sources = list(by_url.values())[: max(req.results_per_query * req.max_queries_per_item, 10)]

        # re-number to unique s1..sN after de-dupe so citations are stable
        sources = [s.model_copy(update={"id": f"s{i+1}"}) for i, s in enumerate(sources)]
        
        try:
            item_report = await _llm_write_report(req, it, sources)
        except Exception as e:
            print(f"[lookup] report failed for claim={it.claim!r}: {e}")
            item_report = LookupItemReport(
                claim=it.claim,
                why_check=it.why_check,
                findings=_fallback_findings_from_sources(sources, max_n=3),
                sources=sources,
            )
        items_out.append(item_report)
    
    debug = {
        "items_planned": len(plan.items),
        "queries": [
            {"claim": it.claim, "queries": it.queries}
            for it in plan.items
        ],
    }

    return LookupReport(
        disclaimer=(
            "This is a research aid, not legal advice. It may miss sources or misread context. "
            "Verify against official/primary sources before relying on it."
        ),
        items=items_out,
        debug=debug,
    )
