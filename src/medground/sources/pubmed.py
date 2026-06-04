"""PubMed / NCBI E-utilities async client.

API docs: https://www.ncbi.nlm.nih.gov/books/NBK25500/

Pipeline: esearch (query → PMIDs) → efetch (PMIDs → XML) → parse → Paper.

NCBI rate limits: 3 req/s without API key, 10 req/s with. We stay well under either using a
semaphore + small batch size; efetch supports up to 200 ids per request so this is rarely tight.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import date

import httpx
from lxml import etree
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from medground.config import CONFIG
from medground.models import Paper

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

_RETRY = AsyncRetrying(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_exception_type((httpx.HTTPError,)),
    reraise=True,
)


def _common_params() -> dict[str, str]:
    p = {"tool": CONFIG.ncbi_tool}
    if CONFIG.ncbi_email:
        p["email"] = CONFIG.ncbi_email
    if CONFIG.ncbi_api_key:
        p["api_key"] = CONFIG.ncbi_api_key
    return p


async def esearch(
    client: httpx.AsyncClient,
    query: str,
    retmax: int = 100,
    mindate: str | None = None,
    datetype: str = "pdat",
    sort: str = "relevance",
) -> list[str]:
    """Return PMIDs matching `query`.

    `mindate` is e.g. "2020/01/01"; `datetype` is "pdat" (publication date) or "edat"
    (entrez date — when PubMed added the record). For watches use `edat` so we catch papers
    newly indexed regardless of their nominal pub year, and `sort="date"` so newest come first.
    """
    params = {
        **_common_params(),
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": str(retmax),
        "sort": sort,
    }
    if mindate:
        params["mindate"] = mindate
        params["datetype"] = datetype
    async for attempt in _RETRY:
        with attempt:
            r = await client.get(ESEARCH, params=params)
            r.raise_for_status()
            data = r.json()
    return data.get("esearchresult", {}).get("idlist", [])


async def efetch_xml(client: httpx.AsyncClient, pmids: Iterable[str]) -> bytes:
    ids = ",".join(pmids)
    if not ids:
        return b""
    params = {
        **_common_params(),
        "db": "pubmed",
        "id": ids,
        "retmode": "xml",
        "rettype": "abstract",
    }
    async for attempt in _RETRY:
        with attempt:
            r = await client.get(EFETCH, params=params)
            r.raise_for_status()
            return r.content
    return b""


def _text(node, xpath: str) -> str:
    el = node.find(xpath)
    return (el.text or "").strip() if el is not None and el.text else ""


def _all_text(node, xpath: str) -> list[str]:
    return [(el.text or "").strip() for el in node.findall(xpath) if el is not None and el.text]


def _parse_year(article) -> tuple[int | None, date | None]:
    # Try PubDate (most reliable), then ArticleDate.
    for xp in (
        ".//Journal/JournalIssue/PubDate",
        ".//ArticleDate",
    ):
        node = article.find(xp)
        if node is None:
            continue
        y = _text(node, "Year")
        m = _text(node, "Month") or "1"
        d = _text(node, "Day") or "1"
        if y and y.isdigit():
            year = int(y)
            try:
                # Month may be a name ("Jan") — fall back gracefully.
                month = int(m) if m.isdigit() else _month_name(m)
                day = int(d) if d.isdigit() else 1
                return year, date(year, month, day)
            except (ValueError, TypeError):
                return year, None
    return None, None


def _month_name(m: str) -> int:
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    return months.get(m.lower()[:3], 1)


def _join_abstract(article) -> str:
    """PubMed abstracts may be split into labeled sections. Concatenate, preserving labels."""
    parts: list[str] = []
    for ab in article.findall(".//Abstract/AbstractText"):
        label = ab.get("Label") or ab.get("NlmCategory")
        text = "".join(ab.itertext()).strip()
        if not text:
            continue
        parts.append(f"{label}: {text}" if label else text)
    return "\n\n".join(parts)


def _authors(article) -> list[str]:
    out = []
    for a in article.findall(".//AuthorList/Author"):
        last = _text(a, "LastName")
        fore = _text(a, "ForeName") or _text(a, "Initials")
        coll = _text(a, "CollectiveName")
        if last:
            out.append(f"{fore} {last}".strip() if fore else last)
        elif coll:
            out.append(coll)
    return out


def _mesh_terms(article) -> list[str]:
    out = []
    for mh in article.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
        t = (mh.text or "").strip()
        if t:
            out.append(t)
    return out


def _ids(article) -> dict[str, str]:
    ids: dict[str, str] = {}
    for aid in article.findall(".//ArticleIdList/ArticleId"):
        kind = (aid.get("IdType") or "").strip()
        val = (aid.text or "").strip()
        if kind and val:
            ids[kind] = val
    return ids


def parse_pubmed_xml(xml_bytes: bytes) -> list[Paper]:
    """Parse a PubmedArticleSet payload into `Paper` records.

    Tolerates `PubmedBookArticle` entries by skipping them — book chapters lack the fields we use
    and aren't useful for clinical-evidence retrieval.
    """
    if not xml_bytes:
        return []
    root = etree.fromstring(xml_bytes)
    papers: list[Paper] = []
    for art in root.findall(".//PubmedArticle"):
        medline = art.find(".//MedlineCitation")
        if medline is None:
            continue
        pmid = _text(medline, "PMID")
        if not pmid:
            continue
        article = medline.find(".//Article")
        if article is None:
            continue
        title = "".join(article.find("ArticleTitle").itertext()).strip() \
            if article.find("ArticleTitle") is not None else ""
        abstract = _join_abstract(article)
        journal = _text(article, ".//Journal/Title") or _text(article, ".//Journal/ISOAbbreviation")
        year, pub_date = _parse_year(article)
        ids = _ids(art)
        doi = ids.get("doi")
        pmcid = ids.get("pmc")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        pub_types = _all_text(article, ".//PublicationTypeList/PublicationType")
        keywords = _all_text(medline, ".//KeywordList/Keyword")
        language = _text(article, ".//Language") or None

        papers.append(
            Paper(
                id=f"pubmed:{pmid}",
                source="pubmed",
                native_id=pmid,
                title=title,
                abstract=abstract,
                authors=_authors(article),
                journal=journal or None,
                year=year,
                publication_date=pub_date,
                doi=doi,
                pmid=pmid,
                pmcid=pmcid,
                url=url,
                mesh_terms=_mesh_terms(medline),
                keywords=keywords,
                publication_types=pub_types,
                language=language,
                raw=None,  # set externally if caller wants to keep XML
            )
        )
    return papers


async def fetch(
    query: str,
    *,
    max_results: int = 50,
    batch_size: int = 100,
    mindate: str | None = None,
    datetype: str = "pdat",
    sort: str = "relevance",
    skip_pmids: set[str] | None = None,
) -> AsyncIterator[Paper]:
    """High-level: yield `Paper` objects for `query`. Streams batches; safe for large pulls.

    `skip_pmids` lets the caller filter out PMIDs already in their corpus before paying for efetch.
    Watches use this to keep delta runs cheap.
    """
    timeout = httpx.Timeout(CONFIG.http_timeout_s)
    limits = httpx.Limits(max_connections=CONFIG.http_concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:
        pmids = await esearch(
            client, query, retmax=max_results, mindate=mindate, datetype=datetype, sort=sort
        )
        if skip_pmids:
            pmids = [p for p in pmids if p not in skip_pmids]
        if not pmids:
            return
        # Polite pacing — NCBI tolerates much more with a key, but this keeps us safe everywhere.
        sem = asyncio.Semaphore(CONFIG.http_concurrency)

        async def _one_batch(batch: list[str]) -> list[Paper]:
            async with sem:
                xml = await efetch_xml(client, batch)
            return parse_pubmed_xml(xml)

        batches = [pmids[i : i + batch_size] for i in range(0, len(pmids), batch_size)]
        for coro in asyncio.as_completed([_one_batch(b) for b in batches]):
            for paper in await coro:
                yield paper
