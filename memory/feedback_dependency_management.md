---
name: feedback-dependency-management
description: Don't try to resolve dependencies or hunt for library source; just write code that uses env vars/APIs as documented
metadata:
  type: feedback
---

Do not try to resolve dependency conflicts or hunt down library source files to understand an API. Just write the code that calls the API as documented or as the user describes, using env vars for configuration.

**Why:** User explicitly said "stop trying to manage dependencies. please just write the code to be able to use the voice clone, and I can clone my voice on the website and pass an ID."

**How to apply:** When a library's cloning/upload API is unclear, write code that consumes the output (e.g., a voice ID the user provides) rather than trying to automate the cloning step itself. Ask the user what env var they'll set, then write the code that reads it.
