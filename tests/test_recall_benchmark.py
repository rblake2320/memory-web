"""
MemoryWeb Golden Test Set (Phase 2a)

50 hand-curated question-answer pairs covering all memory categories.
These are derived from the actual conversation history stored in MemoryWeb.

Categories covered:
  - configuration: specific values, ports, paths, versions
  - infrastructure: system topology, hosts, IPs
  - decision: architectural and technical choices made
  - preference: user preferences and workflow habits
  - learning: insights, lessons, postmortems
  - temporal: "what changed about X?" queries
  - entity: "what is X?" entity-centric queries
  - multi-hop: queries requiring indirect reasoning

Used by benchmark_recall.py to measure end-to-end recall.
Run after each phase to verify improvements don't regress.
"""

GOLDEN_TEST_CASES = [
    # -------------------------------------------------------------------------
    # CONFIGURATION
    # -------------------------------------------------------------------------
    {
        "query": "What port does PostgreSQL run on?",
        "expected_facts": ["port 5432", "PostgreSQL 5432"],
        "category": "configuration",
        "notes": "Core infrastructure config — should be recall@1",
    },
    {
        "query": "What is the PostgreSQL database password?",
        "expected_facts": ["Booker78", "postgres password"],
        "category": "configuration",
        "notes": "Credential recall",
    },
    {
        "query": "What port does MemoryWeb run on?",
        "expected_facts": ["port 8100", "8100"],
        "category": "configuration",
        "notes": "Service port config",
    },
    {
        "query": "What embedding model does MemoryWeb use?",
        "expected_facts": ["all-MiniLM", "sentence-transformers", "384"],
        "category": "configuration",
        "notes": "Model config",
    },
    {
        "query": "Where is the D drive PostgreSQL data directory?",
        "expected_facts": ["D:\\PostgreSQL\\data", "D:/PostgreSQL/data"],
        "category": "configuration",
        "notes": "Path config",
    },
    {
        "query": "What Python version is installed?",
        "expected_facts": ["3.12", "Python 3.12"],
        "category": "configuration",
        "notes": "Runtime version",
    },
    {
        "query": "What is the JupyterLab port?",
        "expected_facts": ["8888", "localhost:8888"],
        "category": "configuration",
        "notes": "Dev tool port",
    },
    {
        "query": "What port does AgentForge run on?",
        "expected_facts": ["8400", "agentvault"],
        "category": "configuration",
        "notes": "Project service port",
    },
    {
        "query": "What Celery beat schedule runs requeue_stalled?",
        "expected_facts": ["10 minutes", "10min", "every 10"],
        "category": "configuration",
        "notes": "Celery schedule config",
    },
    {
        "query": "What is the embedding batch size in the worker?",
        "expected_facts": ["50", "BATCH_SIZE", "batch size 50"],
        "category": "configuration",
        "notes": "Worker config value",
    },

    # -------------------------------------------------------------------------
    # INFRASTRUCTURE
    # -------------------------------------------------------------------------
    {
        "query": "What is the IP address of Spark-1?",
        "expected_facts": ["192.168.12.132", "Spark-1"],
        "category": "infrastructure",
        "notes": "Network topology",
    },
    {
        "query": "What GPU does the Windows workstation have?",
        "expected_facts": ["RTX 5090", "5090"],
        "category": "infrastructure",
        "notes": "Hardware spec",
    },
    {
        "query": "How much RAM does the Windows PC have?",
        "expected_facts": ["128GB", "128 GB"],
        "category": "infrastructure",
        "notes": "Hardware spec",
    },
    {
        "query": "What is the VPS IP address?",
        "expected_facts": ["76.13.118.222"],
        "category": "infrastructure",
        "notes": "Remote server IP",
    },
    {
        "query": "What is the D drive capacity?",
        "expected_facts": ["3TB", "3 TB"],
        "category": "infrastructure",
        "notes": "Storage capacity",
    },
    {
        "query": "How do you SSH to Spark-2 via jump host?",
        "expected_facts": ["ssh -J", "10.0.0.2", "jump"],
        "category": "infrastructure",
        "notes": "SSH jump host config",
    },
    {
        "query": "What OS does Spark-1 run?",
        "expected_facts": ["Ubuntu", "Linux"],
        "category": "infrastructure",
        "notes": "OS identification",
    },
    {
        "query": "Where is the Ultra RAG production deployment?",
        "expected_facts": ["Spark-1", "8300", "~/ultra-rag"],
        "category": "infrastructure",
        "notes": "Service location",
    },

    # -------------------------------------------------------------------------
    # DECISIONS
    # -------------------------------------------------------------------------
    {
        "query": "Why was the memory synthesizer truncating to 3000 characters?",
        "expected_facts": ["truncat", "3000", "silent", "94%"],
        "category": "decision",
        "notes": "Root cause of data loss bug",
    },
    {
        "query": "Why does MemoryWeb use pgvector for embeddings?",
        "expected_facts": ["pgvector", "cosine", "vector search"],
        "category": "decision",
        "notes": "Architecture decision",
    },
    {
        "query": "Why is the embedding worker a daemon thread instead of Celery task?",
        "expected_facts": ["daemon", "thread", "embedding_worker"],
        "category": "decision",
        "notes": "Design decision rationale",
    },
    {
        "query": "Why was the 10-fact cap on memory synthesis a problem?",
        "expected_facts": ["cap", "10", "silently dropped", "facts"],
        "category": "decision",
        "notes": "Bug discovery context",
    },
    {
        "query": "Why should D drive be used for large files instead of C drive?",
        "expected_facts": ["C: nearly full", "D: has 3TB", "space"],
        "category": "decision",
        "notes": "Storage strategy decision",
    },

    # -------------------------------------------------------------------------
    # PREFERENCES
    # -------------------------------------------------------------------------
    {
        "query": "How does the user prefer AI to handle obvious fixes?",
        "expected_facts": ["act immediately", "don't ask", "obvious"],
        "category": "preference",
        "notes": "Workflow preference",
    },
    {
        "query": "What is the preferred domain extension for new projects?",
        "expected_facts": [".app", ".dev", "not .ai"],
        "category": "preference",
        "notes": "Domain preference",
    },
    {
        "query": "How should Gherkin test files be organized?",
        "expected_facts": ["one scenario per file", "ONE scenario"],
        "category": "preference",
        "notes": "Testing preference",
    },

    # -------------------------------------------------------------------------
    # LEARNING / LESSONS
    # -------------------------------------------------------------------------
    {
        "query": "What did we learn from the requeue_stalled infinite retry bug?",
        "expected_facts": ["infinite retry", "Ollama down", "attempt counter"],
        "category": "learning",
        "notes": "Bug lesson learned",
    },
    {
        "query": "What was the NVIDIA NIM miss postmortem about?",
        "expected_facts": ["NIM", "missed", "scan existing", "infrastructure"],
        "category": "learning",
        "notes": "Postmortem lesson",
    },
    {
        "query": "What causes the dict-wrapper bug in memory synthesis?",
        "expected_facts": ["dict", "wrapper", "LLM", "facts", "memories"],
        "category": "learning",
        "notes": "Bug pattern",
    },
    {
        "query": "What is the MemoryWeb pgvector HNSW ef_search setting?",
        "expected_facts": ["100", "ef_search", "hnsw"],
        "category": "learning",
        "notes": "Performance tuning decision",
    },

    # -------------------------------------------------------------------------
    # TEMPORAL (what changed queries)
    # -------------------------------------------------------------------------
    {
        "query": "What changed in migration 004?",
        "expected_facts": ["idempotency", "unique constraint", "004"],
        "category": "temporal",
        "notes": "Migration history",
    },
    {
        "query": "When was the last Celery migration applied?",
        "expected_facts": ["004", "migration", "celery"],
        "category": "temporal",
        "notes": "Timeline query",
    },
    {
        "query": "What is the latest MemoryWeb status?",
        "expected_facts": ["701 sources", "1147 memories", "1225 embeddings"],
        "category": "temporal",
        "notes": "Current state query",
    },
    {
        "query": "What phases has MemoryWeb gone through?",
        "expected_facts": ["phase", "migration", "003", "004"],
        "category": "temporal",
        "notes": "Project history",
    },

    # -------------------------------------------------------------------------
    # ENTITY (what is X)
    # -------------------------------------------------------------------------
    {
        "query": "What is Spark-1?",
        "expected_facts": ["GB10", "119.7GB", "192.168.12.132", "cluster"],
        "category": "entity",
        "notes": "Entity identification",
    },
    {
        "query": "What is MemoryPulse?",
        "expected_facts": ["TUI", "Textual", "D:\\memory-pulse", "memory-pulse"],
        "category": "entity",
        "notes": "Project identification",
    },
    {
        "query": "What is Ultra RAG?",
        "expected_facts": ["RAG", "ultrarag.app", "8300", "Spark-1"],
        "category": "entity",
        "notes": "Project identification",
    },
    {
        "query": "What is AgentForge?",
        "expected_facts": ["agentvault", "D:\\agentvault", "8400", "identity"],
        "category": "entity",
        "notes": "Project identification",
    },
    {
        "query": "What is AI Army OS?",
        "expected_facts": ["8500", "autonomous", "Spark-1", "agent"],
        "category": "entity",
        "notes": "Platform identification",
    },
    {
        "query": "What is the IMDS AutoQA project?",
        "expected_facts": ["D:\\imds-autoqa", "Air Force", "IMDS", "Maven"],
        "category": "entity",
        "notes": "Project identification",
    },
    {
        "query": "Who is rblake2320?",
        "expected_facts": ["Spark", "ssh", "rblake2320"],
        "category": "entity",
        "notes": "Identity resolution",
    },

    # -------------------------------------------------------------------------
    # MULTI-HOP (require indirect reasoning)
    # -------------------------------------------------------------------------
    {
        "query": "What GPU does the machine running MemoryWeb have?",
        "expected_facts": ["RTX 5090", "5090"],
        "category": "multi-hop",
        "notes": "MemoryWeb → Windows PC → RTX 5090",
    },
    {
        "query": "What database does Ultra RAG use on Spark-1?",
        "expected_facts": ["PostgreSQL", "pgvector", "rag schema"],
        "category": "multi-hop",
        "notes": "Ultra RAG → Spark-1 → PostgreSQL",
    },
    {
        "query": "How do you restart the Cloudflare tunnel for Ultra RAG?",
        "expected_facts": ["systemctl", "cloudflared", "restart"],
        "category": "multi-hop",
        "notes": "Service → restart command",
    },
    {
        "query": "What models does Ollama serve on Spark-1?",
        "expected_facts": ["43 models", "ollama.ultrarag.app", "11434"],
        "category": "multi-hop",
        "notes": "Service → model count",
    },
    {
        "query": "What happens when you run start_persistent.ps1?",
        "expected_facts": ["MemoryWeb", "8100", "uvicorn", "kills orphans"],
        "category": "multi-hop",
        "notes": "Script → action chain",
    },

    # -------------------------------------------------------------------------
    # SOLUTION/TROUBLESHOOTING
    # -------------------------------------------------------------------------
    {
        "query": "How do you fix a stale MemoryWeb server on Windows?",
        "expected_facts": ["kill", "python", "50MB", "restart"],
        "category": "solution",
        "notes": "Troubleshooting procedure",
    },
    {
        "query": "How do you run the IMDS Maven build?",
        "expected_facts": ["run_mvn.sh", "JAVA_HOME", "maven"],
        "category": "solution",
        "notes": "Build procedure",
    },
    {
        "query": "What is the fix for the DetachedInstanceError in tagger.py?",
        "expected_facts": ["DetachedInstanceError", "seg IDs", "inside session"],
        "category": "solution",
        "notes": "Bug fix procedure",
    },
    {
        "query": "How do you start the MemoryWeb Celery worker?",
        "expected_facts": ["celery", "-A app.celery_app", "worker", "-P threads"],
        "category": "solution",
        "notes": "Service startup procedure",
    },
]


# Sanity check
assert len(GOLDEN_TEST_CASES) == 50, f"Expected 50 test cases, got {len(GOLDEN_TEST_CASES)}"

if __name__ == "__main__":
    print(f"Golden test set: {len(GOLDEN_TEST_CASES)} cases")
    categories = {}
    for tc in GOLDEN_TEST_CASES:
        cat = tc["category"]
        categories[cat] = categories.get(cat, 0) + 1
    for cat, count in sorted(categories.items()):
        print(f"  {cat:20s}: {count}")
