"""Application-layer agent adapters built on the Cognitive Runtime.

These live under ``apps/`` so they may compose brain-layer pieces (the runtime,
the Plugin SDK, the MissionSpec review path) that must not depend on each other
directly; the dependency direction stays apps -> brain.
"""
