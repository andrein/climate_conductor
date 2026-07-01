# Climate Conductor — Architecture

Design of record: the routing model and the behaviour that follows from it.

## 1. Router, not regulator

The members (e.g. an AC, a hydronic floor thermostat) are already full
`climate` entities that **regulate themselves**. Climate Conductor therefore
does **no** temperature-comparison logic of its own:

- It **owns** the selected `hvac_mode` as **authoritative state**, never
  re-derived from the members' current states.
- On a mode change it drives the single member that serves the mode and **turns
  every other member off**, then **adopts that member's own setpoint** (each
  device keeps its per-mode setpoint). A setpoint set on the group is forwarded
  to the active member and held as authoritative until the next mode change.
- It **mirrors** the active member's state (mode/action/setpoint/fan/…) back for
  display.

No demand loop means no fighting the members' own thermostats, and no
intermittent/missed commands.

## 2. Configuration is the routing table

The one first-class concept is a routing table:

```
hvac_mode → member        # v1: exactly one member per mode
```

The stored config (`CONF_ROUTES`) **is** the runtime model — no heaters/coolers
lists, no isolation rules, no priority side-tables. The set of members is
derived from the table (`set(routes.values())`).

**Config flow:**
1. Pick member entities, an optional temperature-sensor override, and the
   hide-members toggle.
2. Seed a route for every mode the members collectively support, defaulting to
   the capable member. **Only ask** where more than one member can serve a mode
   (the single genuine choice, e.g. "both AC and floor can heat — which serves
   Heat?").

**Advertised modes** = `OFF` + every mode with a route. Leaving a mode
unrouted simply hides it. `fan_mode` / `swing_mode` / `preset_mode` are **not**
in the table — they pass through from whichever member is currently active.

v1 stores one member per mode (single-select UI) but the value is kept simple
so it can grow to a list per mode later without a data migration.

## 3. Interlock by construction

Because exactly one member is active per mode, "one unit cooling while another
heats" is **unreachable** — there is no state that routes two directions at
once. The interlock is a property of the model, not a rule that has to be
enforced and validated.

`heat_cool` routes entirely to the AC (which handles its own low/high band); the
floor is off. So even `heat_cool` has a single active member.

## 4. Out-of-band changes: normalize through the table

A single listener watches the members. Any change that did **not** originate
from the conductor is normalized:

- **Same mode, new setpoint/fan on the active member** → *adopt* it (update the
  group's displayed state).
- **A member switched to a different mode** → treat it as "set the group to that
  mode", then re-apply routing. Poking the AC to `heat` therefore results in the
  group going to `heat`, the **floor** turning on, and the **AC turning back
  off** — routing is authoritative over direct device pokes.
- **The active member turned off deliberately** → the group goes `off`.

### Echo suppression (mandatory)

Every service call the conductor issues to a member carries a `Context` whose id
is prefixed with `CONDUCTOR_CONTEXT_PREFIX`. The listener ignores any event
whose context is one of ours. Without this, the conductor's own "turn the other
member off" commands would be read as fresh out-of-band changes and re-trigger
routing.

## 5. Availability

- **Advertised `hvac_modes` come from config**, never from live member
  availability — so the picker never flickers when a device drops off Wi-Fi.
- The group is **available while at least one member is available**; it goes
  unavailable only if *all* members are. An unplugged AC never takes down a
  floor-heating group.
- An **inactive** member going unavailable has no effect.
- The **active** member going unavailable (a transient drop, not a deliberate
  off) → the group keeps its selected mode, sits idle, and self-heals when the
  member returns. (Contrast with a deliberate `off`, which takes the group off.)

## 6. Current temperature

`current_temperature` comes from the active member's own reading, unless an
explicit temperature-sensor override is configured (which then wins). When the
group is off there is no active member, so only the override — if configured —
is shown.

## 7. Member visibility

The group exposes its members (via the `entity_id`-list attribute convention the
group platforms use) so Home Assistant's native more-info dialog renders the
managed thermostats **under the card** — you can see, at a glance, which member
the group is driving. This is on by default. `hide_members` is a separate toggle
that marks the member entities hidden in the registry (`hidden_by =
INTEGRATION`), keeping them off dashboards while they stay visible in the
group's more-info. A member the user hid by hand is left untouched, and removing
the helper restores visibility.

## Non-goals (v1)

- **Multiple members per mode** (setpoint aggregation, staggering, "which
  radiator leads"). The table is a list internally so this is a later extension,
  not a rewrite.
- A regulator/demand loop, deadband coordination, sync modes, isolation rules.
- Custom Lovelace cards — member visibility uses native more-info.
