"""Concrete plugins built on the ByteWolf Plugin SDK.

These live under ``apps/`` because they may depend on application-layer readers
(e.g. the dashboard telemetry loader) as well as the SDK framework in
``brain/plugin_sdk``; the dependency direction stays apps -> brain. Every plugin
here is read-only and carries no flight-control path.
"""
