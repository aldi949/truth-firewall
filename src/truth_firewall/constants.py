"""Named V1 bounds. Call sites use these names instead of raw numbers."""

# Evidence older than this cannot support VERIFIED.
DEFAULT_FRESHNESS_SECONDS = 60 * 60

# Imported evidence files above this size are refused before parsing.
MAX_EVIDENCE_FILE_BYTES = 1_000_000

# Stored command output cap so one run cannot fill the disk or a prompt.
MAX_TEXT_FIELD_CHARS = 200_000

# Filesystem paths and evidence identifiers longer than this are rejected.
MAX_PATH_CHARS = 4_096

# Maximum evidence records loaded for a single check.
MAX_EVIDENCE_RECORDS = 500

# Maximum characters accepted for an evidence payload after structured decoding.
MAX_EVIDENCE_PAYLOAD_CHARS = 1_000_000

# Maximum claims accepted from one extractor pass.
MAX_CLAIMS = 100

# Largest evidence snippet placed in one LLM request.
MAX_LLM_EVIDENCE_SNIPPET_CHARS = 4_000

# Candidate evidence items returned per claim.
MAX_CANDIDATES = 8

# Minimum retriever score required to keep a candidate.
MIN_RETRIEVAL_SCORE = 2

# Excerpt stored beside a filesystem hash.
MAX_SNAPSHOT_CHARS = 20_000

# Subprocess timeout for explicit collector commands.
COLLECTOR_TIMEOUT_SECONDS = 30

# Edit snippets kept from a host file-edit event.
MAX_EDIT_SNIPPET_CHARS = 2_000
