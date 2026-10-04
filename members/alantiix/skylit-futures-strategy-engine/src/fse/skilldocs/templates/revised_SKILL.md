---
name: $name
description: $description
---

# Revised skill draft, $draft_date

This file is a Revised_Draft for Operator review. It is not SKILL.md. Its content reaches SKILL.md only through `fse drafts promote`, after the Operator records an approval of this exact file in `approvals.yaml`.

## Narrator rules

The Narrator restates Finding_Card data as prose and adds no level, price, Grade or performance figure that the Finding_Card does not contain.

The Strategy_Engine makes every entry and exit decision. The Narrator does not take, skip, size or exit a trade, does not place, modify or cancel an order, and does not change the Order_Mode.

## How the figures were measured

$measurement

Every figure in this file is a measurement of past sessions. It is not a forecast, and it says nothing about how Practice or Combine trading would turn out.

## Holdout_Period evaluation

$holdout

## Rules measured on the pre-holdout sessions

Each Pattern, Gate, Exit_Mode and Kill_Switch of the Playbook_Baseline has one label:

- Kept: enabled in the chosen Strategy_Config with the Playbook_Baseline parameter values.
- Changed: enabled in the chosen Strategy_Config, but disabled in the Playbook_Baseline or set to a different parameter value. The old and new values are shown.
- Removed: disabled in the chosen Strategy_Config.

Each rule shows two figures, the chosen config's value minus the value of a comparison config that differs only in that rule. The comparison disables the rule for Kept, and sets it to its Playbook_Baseline setting for Changed or Removed.

$rules

## Other settings that differ from the Playbook_Baseline

These keys are not part of a labeled rule. They have no figure of their own.

$other

## Unmeasured rules

These rules of the Skill_Documents are not codified by any Pattern, Gate, Exit_Mode or Kill_Switch of the Playbook_Baseline (`docs/traceability.md`). They have no measured figure.

$unmeasured
