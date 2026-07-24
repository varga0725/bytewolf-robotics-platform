"""Vision domain: the shared, versioned observation contracts a consumer reads.

This package holds the consumer side of the Vision contract layer -- the loaders
for the ``DetectionEvent``, ``VisionSummary`` and ``VisionHealth`` events the
Vision stack publishes. It is observation-only: nothing here reaches, or can
produce, a flight command. The producing Vision pipeline lives on its own branch
and serialises to these same schemas; consumers depend only on the versioned
contract, never on the Vision internals.
"""
