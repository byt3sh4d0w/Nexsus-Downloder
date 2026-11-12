# Design System — Nexus Downloader v10

**Status:** Current UI patterns plus explicit future design requirements.  
**Reference:** user-provided network-combiner dashboard image.  
**Framework:** PySide6/Qt; this is a desktop application, not a web UI.

## 1. Visual direction

Use a calm, technical, high-contrast desktop dashboard. The reference uses dark charcoal surfaces, a large throughput summary, flowing interface paths, a compact active-download card, and a per-network contribution list. Avoid generic SaaS layouts, excessive gradients, decorative metrics, and fake activity.

Principles: real data first; visual hierarchy; low-noise animation; consistency; keyboard accessibility; no UI freeze during transfers.

## 2. Color tokens

Current theme implementation uses centralized values in `gui/theme.py` and a cyan/blue accent. Do not assume orange or theme selection is already implemented.

Proposed theme tokens:

| Token | Default direction | Usage |
|---|---|---|
| `background` | charcoal `#111315`-like | main window background |
| `surface` | dark slate `#181a1e`-like | primary cards |
| `surface-elevated` | slightly lighter slate | dialogs and raised panels |
| `border` | subtle neutral gray | panel boundaries and separators |
| `text-primary` | near-white | titles and main values |
| `text-secondary` | cool gray | labels and metadata |
| `accent` | orange by default | active paths, primary action, progress |
| `accent-secondary` | complementary orange/amber | chart fill or secondary emphasis |
| `success` | accessible green | successful completion only |
| `warning` | amber | degraded state or caution |
| `error` | accessible red | failure/destructive state |

Theme implementation should centralize tokens instead of scattering literal colors across widgets. Color values above are design direction; validate contrast and actual rendering before adopting.

### Accent presets
Implemented: orange (default), purple, blue, green, red, teal, pink. On the Light palette the accent is darkened until it reads at 4.5:1 on the surface. The swatch selection ring uses the text colour. Preserve semantic success/warning/error colors independently of the chosen accent.

## 3. Typography

- Use the system UI font for labels and controls.
- Use a monospaced or tabular-numeric font treatment for speed, byte counts, percentages, and ETA to reduce visual jitter.
- Use a clear hierarchy: page title, card heading, primary metric, field label, secondary metadata.
- Avoid excessive letter spacing and all-caps except small metric labels.
- Format units consistently; distinguish MB/s from MiB/s.

## 4. Spacing, layout, and shape

Use a consistent spacing scale (for example 4, 8, 12, 16, 24, 32 logical pixels), rounded cards with restrained radii, and thin borders. Use layouts and size policies rather than hardcoded screen coordinates. Respect display scaling and minimum window sizes.

## 5. Dashboard composition

1. **Navigation:** existing Downloads, Torrents, Networks, History sections.
2. **Throughput/combiner card:** per-adapter rate labels and directional animated paths converging into a central combiner; prominent total actual download throughput; compact bounded history chart.
3. **Active download card:** file type/name, bytes, percentage, state, speed/ETA, destination and available actions.
4. **Network contribution panel:** interface name, state, selected indicator, actual attributed throughput, contribution share when meaningful, and failure/degraded indication.
5. **Download list:** existing operational list and toolbar remain functional; redesign must not hide important actions.

If a measurement is unavailable, show an explicit unknown/unavailable state instead of a fabricated value.

## 6. Reusable components

- Metric card: label, value, unit, optional trend/history.
- Network row: interface identity, state, selection, rate, contribution bar, errors.
- Download row/card: file identity, progress, state, actions.
- Status badge: queued, downloading, paused, completed, failed, seeding, offline.
- Progress bar: accessible numeric progress plus visual fill.
- Empty state: concise explanation and relevant action.
- Error panel: plain-language summary, actionable recovery, optional technical detail.
- Theme preset selector: visible color swatches with text/accessibility names and selected state.

Reuse existing widgets where they meet these rules; do not duplicate controls unnecessarily.

## 7. Interaction and animation

- Animate interface dashes/particles toward the combiner only when a selected adapter is genuinely contributing to the current download.
- Derive motion intensity or flow rate from measured data with smoothing and hard bounds.
- Idle/disconnected/disabled interfaces must not appear active.
- Keep animation subtle and nonessential; all values remain understandable without animation.
- Use bounded update rates and bounded chart history. Avoid a full-window repaint for every metric update.
- Provide a reduced-motion/disable-animation preference as planned functionality.
- Theme changes must not restart, pause, or lose downloads.
- Dialogs, progress, cancellation, and destructive actions must communicate their result clearly.

## 8. Settings and forms

Preserve current settings sections (General, Downloads, Network, Torrents, Advanced). Add Appearance only when implemented and keep it consistent with the current settings dialog. Controls must have labels, appropriate ranges/defaults, clear validation, and explicit Save/Cancel behavior consistent with existing conventions.

## 9. Loading, empty, and error states

- Loading: explain what is being discovered/resolved and keep cancellation available where safe.
- Empty: describe the next useful action; do not fill the UI with fake sample downloads.
- Offline: distinguish adapter link state from Internet reachability.
- Failed transfer: retain actionable error context and preserve safe recovery data.
- Unknown progress: use indeterminate display rather than false precision.
- Completed: show completion only after the backend verifies success.

## 10. Accessibility

- All interactive controls have meaningful accessible names and keyboard focus.
- Keyboard navigation works without relying on color or animation alone.
- Text and controls meet suitable contrast targets; validate rendered combinations.
- Status is conveyed through text/icon plus color, not color alone.
- Support display scaling and resizing.
- Respect reduced-motion preference when added.

## 11. Responsive desktop behavior

Use Qt layouts, splitters, size policies, and sensible minimum sizes. On narrower windows, reduce chart detail or stack cards rather than clip controls. Verify at supported Windows/Linux scaling settings. Do not claim responsive behavior has been tested until it has been exercised.

## 12. Design acceptance criteria

- No fake metrics or sample activity in production UI.
- Animation reflects real backend state and does not block the event loop.
- Every theme preset is readable and consistently applied.
- Theme selection persists after restart.
- Existing download/torrent actions remain discoverable and functional.
- Keyboard navigation, focus, and reduced-motion behavior are tested.

### Themes (implemented)
Settings > Appearance > Theme: Dark (default), Light, System. System follows the OS colour scheme (dark when the OS does not say) and updates live when it changes. All neutral and semantic colours are tokens in `gui/theme.py` (`_DARK`, `_LIGHT`); contrast is checked in tests/test_appearance.py.
