from typing import List, Dict
from app.models.schemas import ResearchOutput
from app.utils.config import load_agents_config
from app.utils.ollama import call_ollama_json


def aggregate_hits_by_doc(hits: List[dict]) -> List[dict]:
    """
    Aggregate chunk-level hits by document ID.
    Returns a list of documents with aggregated scores and match counts.
    """
    agg = {}
    for h in hits:
        doc_id = h.get('doc_id', '')
        score = h.get('score', 0.0)
        text = h.get('text', '')
        title = h.get('title') or h.get('doc_title') or ''
        url = h.get('url') or h.get('source_url') or ''

        if doc_id not in agg:
            agg[doc_id] = {
                'doc_id': doc_id,
                'title': title,
                'url': url,
                'best_score': score,
                'total_score': score,
                'match_count': 1,
                'snippet': text[:500] if text else ''  # Keep first 500 chars of best snippet
            }
        else:
            agg_doc = agg[doc_id]
            agg_doc['total_score'] += score
            agg_doc['match_count'] += 1
            if score > agg_doc['best_score']:
                agg_doc['best_score'] = score
                agg_doc['snippet'] = text[:500] if text else ''

    docs = list(agg.values())
    # Sort by best_score first, then total_score as tiebreaker
    docs.sort(key=lambda d: (d['best_score'], d['total_score']), reverse=True)
    return docs


async def summarize_research(search_results: dict) -> ResearchOutput:
    config = load_agents_config()
    system_prompt = config["agents"]["research"]["system_prompt"]
    prompt = (
        f"{system_prompt}\n\nSearch results: {search_results}\n\n"
        "Return ONLY a single JSON object with double quotes and no markdown or extra text.\n"
        "Required keys and values:\n"
        '- "hits": array of objects with keys "doc_id", "chunk_id", "url", "title", "score", "text".\n'
        '- "total_results": integer.\n'
    )
    return await call_ollama_json(prompt, ResearchOutput)
