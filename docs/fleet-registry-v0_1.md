# Fleet registry v0.1

The fleet registry is the durable inventory of physical robots. It is separate
from the digital twin (a model/configuration) and world memory (perishable
environment evidence). It never commands a vehicle.

Each robot receives one permanent `platform_id` UUID. Its flight controller is
a replaceable component: a USB bench records the observed MAVLink `uid2` as a
hardware identity, plus firmware/product facts and a SHA-256 link to the exact
bench artifact. An all-zero UID is refused; it is not an identity.

Register an X500 only after an identity-bearing bench run:

```bash
.venv/bin/python -m brain.cli.register_usb_bench \
  --artifact simulation/artifacts/hardware-bench/<identity-bearing-artifact>.json \
  --name 'X500 V2 physical 01' --kind aerial_multirotor \
  --twin-id x500v2_reference_01
```

The generated UUID is printed once and stored in `var/fleet/registry.json`.
Supply it with `--platform-id` for later evidence. The registry rejects a UID
that is already assigned to another platform and refuses artifacts outside the
configured hardware-bench directory. Future robots use another `kind` (for
example `ground_robot` or `humanoid`); the inventory model is not aircraft-only.
