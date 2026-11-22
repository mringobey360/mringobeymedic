# Location Verification V28

## Design
- Branch coordinates are the attendance anchor.
- Employees are checked only at attendance/presence actions; there is no continuous tracking.
- Browser geolocation uses `watchPosition`, high accuracy, fresh readings and a 20-second acquisition window.
- The best reading is submitted with its accuracy. Server-side validation remains authoritative.
- Readings with accuracy above 250m or missing accuracy are rejected for automatic verification.
- The employee must also be within the branch's configured geofence radius (30m–2000m).
- If GPS is unavailable, the employee may request manager review; approval is explicit and auditable.
- Managers should capture a branch anchor while physically at the branch, preferably from a phone with precise location enabled.
- Branch GPS capture stores capture time, capture accuracy, capture user and source (DEVICE/MANUAL).
- HTTPS is required in production for browser geolocation.

## Recommended operating policy
1. Capture branch GPS once while physically inside the workplace.
2. Use 100–150m radius in normal urban locations; increase only where GPS conditions require it.
3. Do not rely on IP address as a substitute for GPS.
4. For desktops without good location hardware, use a phone for branch setup and attendance.
5. Keep manager review as the controlled fallback rather than silently accepting missing location.
