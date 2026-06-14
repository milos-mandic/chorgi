# Scheduling Preferences

## Working Hours
- Weekdays: 9:00 AM - 6:00 PM
- Weekends: Fully available

These are the hours you are *at work*. The task auto-planner uses them to decide
which free calendar gaps each task may fill, based on the task's time class.

## Task Time Classes
The auto-planner fills any free gap on the calendar, but a task's time class
narrows which gaps qualify. The exact time always depends on real availability.
- anytime (default): any free slot 8:00 AM - 10:00 PM, any day — calls, quick/flexible tasks.
- work_hours: weekdays within working hours only — banks, offices, anything needing a business open.
- off_hours: weekday evenings (after working hours, until 10:00 PM) + all weekend — errands, chores, personal admin, exercise.

## Defaults
- Default task duration: 60 minutes
- Buffer between events: 15 minutes
- Default importance: medium
- Scheduling horizon (no date given): next 14 days

## Attendees
- Always invite the calendar owner (CALENDAR_OWNER_ID) so events appear on their personal calendar
