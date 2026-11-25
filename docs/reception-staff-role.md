# Reception staff access

Run `python manage.py setup_reception_staff_group` in the Django environment
where you want to provision the **Reception staff** group. The command provides
handover and transcript access, including replies, assignments and marking
messages as read. It also provides read-only access to bookings and booking
confirmations.

An administrator can add an existing staff account to the group through
**Users**. Django combines group permissions with any permissions the account
already has. Review those grants when assigning access. Group setup leaves
user memberships and staff flags alone.

The command can be rerun on a matching group. It refuses to modify a group
with extra permissions or a group with members that is missing permissions.
Review conflicts in the private admin before changing access.

## Parser reviewer access

Run `python manage.py setup_parser_reviewer_group` to provision the separate
**Parser reviewer** group. It grants permission to view parser samples and
save corrections. Assign existing staff through **Users**. The same rules
for repeated setup and conflicting groups apply.

## Scenario editor access

Run `python manage.py setup_scenario_editor_group` to provision the **Scenario
editor** group. Members can author scenario drafts, including steps and
transitions, in the private admin. Publishing or rolling back a version requires
the separate scenario publication permission. Assign existing staff through
**Users**.
