## Project Context

This project is part of GT-EduValida and aims to infer a person's technical skills from their contributions to GitHub repositories.

The analysis must focus on the contributor's actual work and link each inferred skill to verifiable evidence. A repository's technology stack alone does not justify attributing skills to a contributor. For example, modifying only CSS or documentation in a Django project does not demonstrate proficiency in Django or Python.

This module focuses on skill extraction from repository contributions. Extracting skills from course syllabi belongs to a separate module.

The architecture and technology stack have not yet been defined. Potential approaches include code analysis, machine learning, transformers, and LLMs. Static rules may support the analysis, but their ability to generalize is a concern.

## Skill Reference Catalog

Skill extraction must use an explicit, versioned reference catalog. Every inferred skill must reference an existing catalog entry and verifiable evidence from the contributor's work. Extractors must not invent skill labels.

Each catalog entry must have a stable ID, name, description, aliases, evidence criteria, and source references with their revisions. roadmap.sh is the initial reference candidate; the catalog must support other sources and consolidate equivalent topics across references.

The system must distinguish insufficient evidence from a catalog coverage gap. Neither outcome establishes that the contributor lacks a skill. Catalog additions require a separate review and must not be silently created during extraction.

A topic's position in a roadmap does not establish proficiency, prerequisites, related skills, or completion of a role. Each assignment requires its own supporting evidence. Do not assign proficiency levels in the initial implementation.

The implementation scope, data contracts, and milestones are documented in docs/spec.md.
