# Climate Conductor

A Home Assistant **helper** that presents several climate entities in one room
as a single thermostat, and routes each HVAC mode to the device that serves it
— heat to the underfloor/boiler, cool to the AC, and so on. Only one member
runs at a time, so one unit can never cool while another heats.

## What it does

- Exposes one `climate` entity standing in for a group of real climate devices.
- Maps each HVAC mode to a single member via a routing table
  (`hvac_mode → member`).
- On a mode/setpoint change, forwards it to that member and turns the rest off.
- Advertises `OFF` plus every routed mode; unrouted modes are hidden.
- Passes `fan_mode` / `swing_mode` / `preset_mode` through from the active
  member.
- Mirrors the active member's state (mode, action, setpoint, fan, …) for
  display.
- Supports hiding the member entities from your dashboards.

## How it works

Climate Conductor is a **router, not a regulator**. It owns the selected HVAC
mode as authoritative state — never re-derived from the members — and drives the
single member that serves that mode, turning the others off. The members
regulate themselves; the group does no temperature logic of its own and mirrors
the active member's setpoint, action, and status back for display. The stored
configuration **is** the routing table, and because only one member is ever
active, the heat/cool interlock holds by construction. Full design:
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Installation

Copy `custom_components/climate_conductor` into your Home Assistant
`config/custom_components/` directory and restart. Then add it from
**Settings → Devices & Services → Helpers → Create Helper → Climate Conductor**.

## Development

```sh
uv sync --group dev
uv run pytest
```

## Credits

Inspired by [`tetele/hvac_group`](https://github.com/tetele/hvac_group) and
[`bjrnptrsn/climate_group_helper`](https://github.com/bjrnptrsn/climate_group_helper).

## License

[Apache License 2.0](LICENSE).
