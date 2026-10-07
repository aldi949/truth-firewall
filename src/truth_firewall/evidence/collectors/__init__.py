"""Local evidence collectors."""

from truth_firewall.evidence.collectors.filesystem import FilesystemCollector
from truth_firewall.evidence.collectors.git import GitCollector
from truth_firewall.evidence.collectors.process import ProcessEvidence
from truth_firewall.evidence.collectors.runtime import RuntimeEventImporter
from truth_firewall.evidence.collectors.test_runner import TestEvidence

__all__ = [
    "FilesystemCollector",
    "GitCollector",
    "ProcessEvidence",
    "RuntimeEventImporter",
    "TestEvidence",
]
