# Conversation memory: summary processor.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
import json
from typing import Dict, Any, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.logging import logger
from app.core.http_client import get_http_client
from app.db.models import ConversationSummaryModel, SummaryAddonModel, MessageModel, ConversationModel, EntityModel
from app.providers.openrouter import OpenRouterProvider

# Live wizard state — never fold into narrative summary
_SKIP_MERGE_TYPES = frozenset({"BOOKING_DRAFT", "LANGUAGE_PREFERENCE"})
_EMPTY_OLD = ("", "no previous summary.", "none")


# Drop empty / placeholder prior summary so it is not copied into the new text.
def _clean_old_text(text: str) -> str:
    t = (text or "").strip()
    if t.lower() in _EMPTY_OLD:
        return ""
    return t


# Dedup identical type+content rows; keep unique facts in order (cap noise).
def _compact_addons(addons: List[Any], cap: int = 10) -> List[Any]:
    seen = set()
    ordered: List[Any] = []
    for a in addons:
        key = ((a.type or "").upper(), (a.content or "").strip().lower())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        ordered.append(a)
    return ordered[-cap:] if len(ordered) > cap else ordered


# Human-readable addon lines for the LLM (not dumped as the summary).
def _addons_for_prompt(addons: List[Any]) -> str:
    lines = []
    for a in addons:
        t = (a.type or "").replace("_", " ").title()
        c = (a.content or "").strip()
        if c:
            lines.append(f"- {t}: {c}")
    return "\n".join(lines) if lines else "None"


# Paragraph + bullets when the LLM merge fails.
def _rule_based_summary(old_text: str, addons: List[Any], msgs: List[Any]) -> Dict[str, Any]:
    facts: List[Dict[str, str]] = []
    bullets: List[str] = []
    for a in addons:
        t = (a.type or "").upper()
        c = (a.content or "").strip()
        if not c:
            continue
        label = t.replace("_", " ").title()
        facts.append({"key": t.lower(), "value": c})
        bullets.append(f"• {label}: {c}")

    intent = "The patient discussed clinic care"
    types = {(a.type or "").upper() for a in addons}
    if "BOOKING_CONTEXT" in types or "APPOINTMENT_PREFERENCE" in types:
        intent = "The patient wanted to book an appointment"
    concern = next(
        ((a.content or "").strip() for a in addons if (a.type or "").upper() == "PATIENT_CONCERN"),
        "",
    )
    screen = next(
        ((a.content or "").strip() for a in addons if (a.type or "").upper() == "PATIENT_SCREENING"),
        "",
    )
    para_bits = [intent]
    if concern:
        para_bits.append(f"main concern: {concern}")
    if screen:
        para_bits.append(f"screening noted ({screen})")
    if old_text:
        para_bits.append(f"earlier notes: {old_text}")
    paragraph = ". ".join(para_bits).rstrip(".") + "."
    summary_text = paragraph
    if bullets:
        summary_text = paragraph + "\n\n" + "\n".join(bullets)
    return {
        "summary_text": summary_text,
        "facts": facts,
        "preferences": [
            (a.content or "").strip()
            for a in addons
            if (a.type or "").upper() in ("APPOINTMENT_PREFERENCE", "SERVICE_INTEREST")
            and (a.content or "").strip()
        ],
        "pending_topics": [],
    }


# Summary processor.
class SummaryProcessor:
    # Initialize instance.
    def __init__(self, provider: OpenRouterProvider = None):
        self.provider = provider or OpenRouterProvider()

    # Merge summary.
    async def merge_summary(
        self,
        db: AsyncSession,
        conversation_id: str
    ) -> Dict[str, Any]:
        # 1. Fetch current summary
        stmt_sum = select(ConversationSummaryModel).where(
            ConversationSummaryModel.conversation_id == conversation_id
        )
        res_sum = await db.execute(stmt_sum)
        current_sum = res_sum.scalars().first()

        # 2. Fetch pending addons (exclude live booking/language state)
        stmt_add = (
            select(SummaryAddonModel)
            .where(
                SummaryAddonModel.conversation_id == conversation_id,
                SummaryAddonModel.status == "PENDING"
            )
        )
        res_add = await db.execute(stmt_add)
        all_pending = res_add.scalars().all()
        pending_addons = [
            a for a in all_pending
            if (a.type or "").upper() not in _SKIP_MERGE_TYPES
        ]
        if not pending_addons:
            return {
                "conversation_id": conversation_id,
                "version": current_sum.version if current_sum else 0,
                "summary_text": current_sum.summary_text if current_sum else "",
                "summary_json": current_sum.summary_json if current_sum else {},
                "merged_addons_count": 0,
            }

        # 3. Fetch recent messages & entity context
        stmt_msg = (
            select(MessageModel)
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at.desc())
            .limit(10)
        )
        res_msg = await db.execute(stmt_msg)
        recent_msgs = list(reversed(res_msg.scalars().all()))

        # Check entity domain
        stmt_conv = select(ConversationModel).where(ConversationModel.id == conversation_id)
        res_conv = await db.execute(stmt_conv)
        conv = res_conv.scalars().first()
        ent = None
        if conv:
            stmt_ent = select(EntityModel).where(EntityModel.id == conv.entity_id)
            res_ent = await db.execute(stmt_ent)
            ent = res_ent.scalars().first()

        is_clinical = ent and ent.type.upper() in ["CLINIC", "DOCTOR"]
        entity_name = ent.name if ent else "the Assistant"

        raw_old = current_sum.summary_text if current_sum else ""
        old_text = _clean_old_text(raw_old)
        old_version = current_sum.version if current_sum else 0
        compact = _compact_addons(pending_addons)
        addons_text = _addons_for_prompt(compact)
        msgs_text = "\n".join([f"{m.sender_type}: {m.content}" for m in recent_msgs]) or "None"

        domain_directive = (
            "Clinical patient care conversation. Keep BP, sugar/diabetes, allergies, "
            "age, gender, service, and slot times if present."
            if is_clinical
            else (
                f"General-purpose {ent.type if ent else 'generic'} assistant ('{entity_name}'). "
                "Do NOT assume or insert medical/clinic references unless explicitly in the text."
            )
        )
        old_block = old_text if old_text else "(none — write a fresh summary, do not say 'No previous summary')"

        prompt = (
            "You write clinic conversation summaries for staff follow-up.\n"
            f"Domain: {domain_directive}\n\n"
            "Rewrite the notes below as a clean staff briefing. "
            "Deduplicate repeats (e.g. many booking/time lines → one latest preference). "
            "Ignore raw tag names like BOOKING_CONTEXT. "
            "Never copy the add-on list verbatim. "
            "Never start with 'No previous summary'.\n\n"
            f"--- EXISTING SUMMARY (v{old_version}) ---\n{old_block}\n\n"
            f"--- FACTS TO MERGE ---\n{addons_text}\n\n"
            f"--- RECENT MESSAGES ---\n{msgs_text}\n\n"
            "Return strictly valid JSON only, with this shape:\n"
            "{\n"
            '  "summary_text": "<one short paragraph, then a blank line, then bullet points>",\n'
            '  "facts": [{"key": "fact_name", "value": "fact_value"}],\n'
            '  "preferences": ["preference"],\n'
            '  "pending_topics": ["open topic if any"]\n'
            "}\n\n"
            "summary_text rules:\n"
            "- Paragraph (2–4 sentences): who they are (if known), why they wrote, "
            "what was booked or still open.\n"
            "- Then bullets, each on its own line starting with • \n"
            "- Use bullets for: concern, service, slot/time, screening (BP / sugar / allergies), "
            "age, gender, language — only if known.\n"
            "- Omit empty bullets. No markdown headings. No JSON inside summary_text.\n"
        )

        try:
            llm_res = await self.provider.generate(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800
            )

            content = (llm_res.content or "").strip()
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0].strip()
            elif content.startswith("```"):
                content = content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(content)
            st = (parsed.get("summary_text") or "").strip()
            if not st or st.lower().startswith("no previous summary") or st.startswith("- ["):
                raise ValueError("summary_text still looks like a raw dump")
        except Exception as err:
            logger.warning(f"LLM summary generation failed: {err}. Using rule-based merge.")
            parsed = _rule_based_summary(old_text, compact, recent_msgs)

        new_version = old_version + 1
        summary_text = parsed.get("summary_text", old_text)

        if current_sum:
            current_sum.version = new_version
            current_sum.summary_text = summary_text
            current_sum.summary_json = parsed
        else:
            current_sum = ConversationSummaryModel(
                conversation_id=conversation_id,
                version=new_version,
                summary_text=summary_text,
                summary_json=parsed,
            )
            db.add(current_sum)

        # Mark all pending addons as MERGED
        for addon in pending_addons:
            addon.status = "MERGED"

        await db.flush()

        # 4. Sync summary to CGS PostgreSQL Database ONLY for CLINIC/DOCTOR entities
        if settings.CGS_INTERNAL_URL:
            try:
                stmt_conv = select(ConversationModel).where(ConversationModel.id == conversation_id)
                res_conv = await db.execute(stmt_conv)
                conv = res_conv.scalars().first()

                if conv:
                    stmt_ent = select(EntityModel).where(EntityModel.id == conv.entity_id)
                    res_ent = await db.execute(stmt_ent)
                    ent = res_ent.scalars().first()

                    # Strictly sync only if entity is CLINIC or DOCTOR
                    if ent and ent.type.upper() in ["CLINIC", "DOCTOR"]:
                        cgs_id = getattr(conv, "cgs_conversation_id", None) or conv.id
                        sync_url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/summary/sync"
                        client = get_http_client()
                        res = await client.post(
                            sync_url,
                            json={
                                "conversationId": cgs_id,
                                "clinicId": conv.entity_id,
                                "participantPhone": conv.participant_id,
                                "version": new_version,
                                "summaryText": summary_text,
                                "topics": parsed.get("pending_topics", []),
                                "entities": {"facts": parsed.get("facts", [])},
                                "keyPoints": parsed.get("preferences", []),
                                "source": "ADDON_MERGE",
                            },
                            headers={
                                "Content-Type": "application/json",
                                "X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY,
                            },
                            timeout=5,
                        )
                        if res.status_code == 404:
                            logger.info(
                                f"CGS summary sync skipped — no conversation "
                                f"id={cgs_id} phone={conv.participant_id}"
                            )
                        elif res.status_code >= 400:
                            logger.warning(
                                f"CGS summary sync {res.status_code}: {res.text[:200]}"
                            )
            except Exception as sync_err:
                logger.warning(f"Could not forward clinic summary to CGS backend: {sync_err}")

        return {
            "conversation_id": conversation_id,
            "version": new_version,
            "summary_text": summary_text,
            "summary_json": parsed,
            "merged_addons_count": len(pending_addons),
        }
