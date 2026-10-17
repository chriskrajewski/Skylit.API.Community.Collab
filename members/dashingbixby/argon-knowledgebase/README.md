# Argon Knowledge Base

- Discord username: dashingbixby
- Project identifier: argon-knowledgebase
- Kind: agent

## What it does

A Markdown knowledge base (22 core files plus a session folder) of the Skylit methodology, written for human reviewers and for software agents. Every rule, threshold, time window, and procedure carries an epistemic tag: `[DOCTRINE: <source>]`, `[GENERAL TA]`, `[INFERENCE]`, or `[NOT SPECIFIED]`. Agents should treat `[NOT SPECIFIED]` parameters as configuration, not constants, and check `open_questions.md`.

Start with [00_index.md](00_index.md). It holds the source inventory, file map, reading order, and the tagging specification.

### Files

| Area | Files |
| --- | --- |
| Index and reference | [00_index.md](00_index.md) · [glossary.md](glossary.md) · [open_questions.md](open_questions.md) · [platform_reference_atlas_heatseeker_talon.md](platform_reference_atlas_heatseeker_talon.md) |
| Chart structure | [chart_structure_fundamentals.md](chart_structure_fundamentals.md) · [charts_first_workflow.md](charts_first_workflow.md) |
| Heatseeker | [gex_vs_vex_and_expirations.md](gex_vs_vex_and_expirations.md) · [trinity_and_cross_index.md](trinity_and_cross_index.md) · [heatseeker_node_core_attributes_gaps.md](heatseeker_node_core_attributes_gaps.md) · [heatseeker_node_spatio_temporal_relationships_gaps.md](heatseeker_node_spatio_temporal_relationships_gaps.md) · [heatseeker_advanced_mechanics_and_agent_guardrails_gaps.md](heatseeker_advanced_mechanics_and_agent_guardrails_gaps.md) |
| Setups | [setup_catalog_rug_and_reverse_rug.md](setup_catalog_rug_and_reverse_rug.md) · [setup_catalog_beach_ball_and_pika_cloud.md](setup_catalog_beach_ball_and_pika_cloud.md) · [setup_catalog_fade_gatekeeper_and_vex_swing.md](setup_catalog_fade_gatekeeper_and_vex_swing.md) · [case_studies.md](case_studies.md) |
| Execution and risk | [entries_stops_targets_and_rr.md](entries_stops_targets_and_rr.md) · [risk_sizing_and_discipline.md](risk_sizing_and_discipline.md) · [no_trade_conditions.md](no_trade_conditions.md) · [session_timing.md](session_timing.md) |
| Other products | [flowseeker_options_flow.md](flowseeker_options_flow.md) · [dark_pool.md](dark_pool.md) · [tempest_volatility.md](tempest_volatility.md) |
| Garma agent video (2026-10-17) | [transcript](GarmaAgentVideo_10172026/garma_agent_video_transcript_10172026.md) · [comparative analysis](GarmaAgentVideo_10172026/garma_agent_video_comparative_analysis_10172026.md) · [reverse rug](GarmaAgentVideo_10172026/garma_agent_video_reverse_rug_10172026.md) |

## Skylit surface

- Access: none (documentation only)
- Products: Heatseeker, Flowseeker, Tempest, Atlas

Sources: Skylit Academy export, [skylit.ai/docs](https://www.skylit.ai/docs), the Skylit changelog, and Skylit Learn articles. See the source inventory in `00_index.md`.

## How to run

Nothing to run. Read the files in the order given in `00_index.md`, or load them as context for an agent.

## Environment variables

None.
