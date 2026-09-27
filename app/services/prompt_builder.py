# Assembles the system prompt from clinic card, memory, and booking state.
# Instructs the model never to leak tools or IDs to the patient.
import json
from typing import List, Dict, Any, Optional
from app.entities.base import EntityContext
from app.memory.memory_engine import MemoryContext


# Prompt builder.
class PromptBuilder:
    # Build prompt messages.
    def build_prompt_messages(
        self,
        entity_ctx: EntityContext,
        memory_ctx: MemoryContext,
        current_user_message: str,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        meta = request_metadata or {}

        # 1. System Prompt Construction
        system_sections = []

        if entity_ctx.type not in ["CLINIC", "DOCTOR"]:
            system_sections.append(
                f"[SYSTEM DIRECTIVE: DOMAIN RESTRICTION]\n"
                f"You are '{entity_ctx.name}', a dedicated {entity_ctx.type.lower()} assistant.\n"
                f"You are NOT associated with any dental clinic, hospital, or medical practice.\n"
                f"STRICT RULE: Do NOT mention clinics, doctors, patients, dental procedures, or medical appointments unless explicitly defined in your entity configuration."
            )

        system_sections.append(entity_ctx.system_prompt)

        # Known lead / landing-page context (from CRM conversation)
        lead_bits = []
        if meta.get("participantName"):
            lead_bits.append(f"Patient name: {meta.get('participantName')}")
        if meta.get("serviceInterested"):
            lead_bits.append(f"Interested service: {meta.get('serviceInterested')}")
        if meta.get("preferredDoctor"):
            lead_bits.append(f"Preferred doctor: {meta.get('preferredDoctor')}")
        if meta.get("howHeardAboutDoctor"):
            lead_bits.append(f"How they found us: {meta.get('howHeardAboutDoctor')}")
        if meta.get("landingContext") or meta.get("leadNotes"):
            lead_bits.append(f"Notes:\n{meta.get('landingContext') or meta.get('leadNotes')}")
        if lead_bits:
            system_sections.append(
                "\n[KNOWN LEAD CONTEXT FROM LANDING / CRM]\n"
                + "\n".join(lead_bits)
                + "\nUse this context — do not re-ask for name/service/doctor if already known. "
                "Age and gender are usually NOT in landing data — ask for both if unknown. Never invent age or gender. "
                "Continue booking and medical screening from here."
            )

        # Add Layer 2: Consolidated Summary
        if memory_ctx.summary:
            sum_text = memory_ctx.summary.get("text", "")
            facts = memory_ctx.summary.get("facts", [])
            prefs = memory_ctx.summary.get("preferences", [])

            summary_block = f"\n[CONVERSATION HISTORY SUMMARY]\n{sum_text}"
            if facts:
                label = "Key Facts" if entity_ctx.type not in ["CLINIC", "DOCTOR"] else "Key Patient Facts"
                summary_block += f"\n{label}: {json.dumps(facts)}"
            if prefs:
                summary_block += f"\nKnown Preferences: {', '.join(prefs)}"
            system_sections.append(summary_block)

        # Add Layer 3: Pending Summary Add-ons + language preference
        lang_prefs: list[str] = []
        if memory_ctx.pending_addons:
            addons_list = [f"- [{a['type']}] {a['content']}" for a in memory_ctx.pending_addons]
            system_sections.append("\n[RECENTLY DISCOVERED FACTS]\n" + "\n".join(addons_list))
            lang_prefs = [
                a["content"]
                for a in memory_ctx.pending_addons
                if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE"
            ]
        if lang_prefs:
            chosen = lang_prefs[-1]
            system_sections.append(
                f"\n[ACTIVE LANGUAGE]\nPatient chose: {chosen}. "
                f"Reply ONLY in {chosen} for the rest of this chat unless they ask to switch."
            )
        else:
            system_sections.append(
                "\n[ACTIVE LANGUAGE]\nLanguage not chosen yet. "
                "If this is early in the chat (first few turns), ask once: Hinglish or English? "
                "Then stick to their answer."
            )

        # Add Layer 4: Live Business Context
        if entity_ctx.live_business_context:
            if entity_ctx.type in ["CLINIC", "DOCTOR"]:
                clinic = entity_ctx.live_business_context.get("clinic") or {}
                doctors = entity_ctx.live_business_context.get("doctors") or []
                services = entity_ctx.live_business_context.get("services") or []
                doctor_line = ", ".join(
                    (
                        f"{d.get('name')} ({d.get('specialization') or 'Consultant'}; "
                        f"days={','.join(d.get('availabilityDays') or []) or 'n/a'}; "
                        f"hours={d.get('availabilityHours') or 'n/a'})"
                    )
                    for d in doctors[:8]
                    if d.get("name")
                )
                service_line = ", ".join(
                    f"{s.get('name')} ₹{s.get('price')}" for s in services[:8] if s.get("name")
                )
                compact = (
                    f"Clinic: {clinic.get('name') or entity_ctx.name}\n"
                    f"Phone: {clinic.get('phone') or 'N/A'}\n"
                    f"Doctors: {doctor_line or 'Use get_doctors'}\n"
                    f"Services: {service_line or 'Use get_services'}\n"
                    "Only quote doctor days/hours from this card or get_doctors. "
                    "Always check_availability before promising a slot."
                )
                system_sections.append(f"\n[LIVE CLINIC CARD]\n{compact}")
            else:
                config = entity_ctx.live_business_context.get("configuration")
                if config and isinstance(config, dict) and len(config) > 0:
                    config_str = json.dumps(config, indent=2)
                    system_sections.append(f"\n[ENTITY CONFIGURATION & KNOWLEDGE]\n{config_str}")
                else:
                    system_sections.append(
                        "\n[INSTRUCTION]\nYou are a general-purpose AI assistant. Do NOT assume, mention, or offer clinic, hospital, doctor, or healthcare appointment services unless explicitly specified in your configuration or asked by the user."
                    )

        # Add Current Timestamp & Timezone (Asia/Kolkata) — always include absolute dates
        # so relative words like "tomorrow" / "next Monday" resolve correctly.
        try:
            from datetime import datetime, timedelta, timezone
            try:
                from zoneinfo import ZoneInfo
                kolkata_tz = ZoneInfo("Asia/Kolkata")
            except Exception:
                kolkata_tz = timezone(timedelta(hours=5, minutes=30), name="IST")

            now_kolkata = datetime.now(kolkata_tz)
            tomorrow = now_kolkata + timedelta(days=1)
            current_time_str = now_kolkata.strftime("%A, %d %B %Y, %I:%M %p IST")
            today_iso = now_kolkata.strftime("%Y-%m-%d")
            tomorrow_iso = tomorrow.strftime("%Y-%m-%d")
            system_sections.append(
                "\n[CURRENT DATE & TIME (Asia/Kolkata)]\n"
                f"Now: {current_time_str}\n"
                f"Today: {today_iso} | Tomorrow: {tomorrow_iso}\n"
                "Use these for relative dates. Pass tools YYYY-MM-DD only."
            )
        except Exception:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            tomorrow = now + timedelta(days=1)
            system_sections.append(
                "\n[CURRENT DATE & TIME (Asia/Kolkata approx)]\n"
                f"Today: {now.strftime('%Y-%m-%d')} | Tomorrow: {tomorrow.strftime('%Y-%m-%d')}"
            )

        system_sections.append(
            "\n[CHECKLIST] Continue thread → info≠book → book only with clear intent + "
            "check_availability → confirm with full details → use patient language.\n"
            "[SLOTS RULE] Never list specific open appointment slots unless the patient "
            "asked about availability/slots or is clearly booking. Doctor days/hours from "
            "the clinic card are OK for info questions; live slot lists are not."
        )

        # Combine into master system instruction
        system_content = "\n\n".join(system_sections)
        messages.append({"role": "system", "content": system_content})

        # Add Layer 1: Recent Conversation Messages
        if memory_ctx.recent_messages:
            messages.extend(memory_ctx.recent_messages)

        # Add Current User Message
        messages.append({"role": "user", "content": current_user_message})

        return messages


prompt_builder = PromptBuilder()
