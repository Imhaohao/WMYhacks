import type { ScreenedCall } from "@/lib/api"

export const EXAMPLE_CALL: ScreenedCall = {
  id: "example",
  received_at: "2026-10-14T16:12:00-07:00",
  duration_seconds: 48,
  caller_name: "Maya Okafor",
  caller_number: "+14155550143",
  reason: "Moving Thursday's apartment viewing to Friday morning",
  urgency: "normal",
  wants_callback: true,
  callback_number: "",
  callback_time: "after 3pm",
  caller_words:
    "Hi, it's Maya from Larkin Street Rentals. Thursday doesn't work for the owner anymore. Could we do Friday at ten instead? Call me back after three.",
  owner_text:
    "Gotchu: Maya Okafor (+14155550143) called about moving Thursday's apartment viewing to Friday morning. Wants a call back after 3pm.",
  texted: true,
}
