# Copyright (C) 2022 Cetmix OÜ
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from operator import indexOf

from odoo import _, api, fields, models
from odoo.tools.safe_eval import expr_eval

from .constants import (
    ANOTHER_PLAN_RUNNING,
    PLAN_LINE_CONDITION_CHECK_FAILED,
    PLAN_LINE_NOT_ASSIGNED,
    PLAN_NOT_ASSIGNED,
)


class CxTowerPlan(models.Model):
    """A sequence of commands running based on the pre-defined flow"""

    _name = "cx.tower.plan"
    _description = "Cetmix Tower Flight Plan"
    _inherit = [
        "cx.tower.reference.mixin",
        "cx.tower.access.mixin",
    ]
    _order = "name asc"

    active = fields.Boolean(default=True)
    allow_parallel_run = fields.Boolean(
        help="If enabled flightplan can be run on the same server "
        "while the same flightplan is still running.\n"
        "Returns -5 status is execution is blocked"
    )

    color = fields.Integer(help="For better visualization in views")
    server_ids = fields.Many2many(string="Servers", comodel_name="cx.tower.server")
    tag_ids = fields.Many2many(string="Tags", comodel_name="cx.tower.tag")
    line_ids = fields.One2many(
        string="Commands",
        comodel_name="cx.tower.plan.line",
        inverse_name="plan_id",
        auto_join=True,
    )
    note = fields.Text()
    on_error_action = fields.Selection(
        string="On Error",
        selection=[
            ("e", "Exit with command exit code"),
            ("ec", "Exit with custom exit code"),
            ("n", "Run next command"),
        ],
        required=True,
        default="e",
        help="This action will be executed on error "
        "if no command action can be applied",
    )
    custom_exit_code = fields.Integer(
        help="Will be used instead of the command exit code"
    )

    access_level_warn_msg = fields.Text(
        compute="_compute_command_access_level",
        compute_sudo=True,
    )

    def execute(self, servers, **kwargs):
        """Execute plans on multiple servers

        Args:
            servers (cx.tower.server()): server records
            kwargs (dict): Optional arguments
                Following are supported but not limited to:
                    - "plan_log": {values passed to flightplan logger}
                    - "log": {values passed to logger}
                    - "key": {values passed to key parser}
        """

        # Execute each plan for each server is list
        for server in servers:
            for plan in self:
                plan._execute_single(server, **kwargs)

    def _execute_single(self, server, **kwargs):
        """Execute Flight Plan

        Args:
            server (cx.tower.server()): Server object
            kwargs (dict): Optional arguments
                Following are supported but not limited to:
                    - "plan_log": {values passed to flightplan logger}
                    - "log": {values passed to logger}
                    - "key": {values passed to key parser}

        Returns:
            status (Int): plan execution status
        """
        self.ensure_one()
        if len(server) > 1:
            raise ValueError(_("_execute() function takes single server record only!"))

        plan_log_obj = self.env["cx.tower.plan.log"].sudo()

        # Check if the same plan is being executed on this server right now
        if not self.allow_parallel_run or self.env.context.get(
            "prevent_plan_recursion"
        ):
            running_count = plan_log_obj.search_count(
                [
                    ("server_id", "=", server.id),
                    ("plan_id", "=", self.id),
                    ("is_running", "=", True),
                ]
            )
            if running_count > 0:
                return ANOTHER_PLAN_RUNNING

        # Start Flight Plan log
        return plan_log_obj.start(
            server, self, fields.Datetime.now(), **kwargs
        ).plan_status

    def _get_next_action_values(self, command_log):
        """Get next action values based of previous command result:

            - Action to proceed
            - Exit code
            - Next line of the plan if next line should be executed

        Args:
            command_log (cx.tower.command.log()): Command log record

        Returns:
            action, exit_code, next_line (Selection, Integer, cx.tower.plan.line())

        """
        # Iterate all actions and return the first matching one.
        # If no action is found return the default plan values
        # If the line is the last one return last command execution code

        if not command_log.plan_log_id:  # Exit with custom code "Plan not found"
            return "ec", PLAN_NOT_ASSIGNED, None

        current_line = command_log.plan_log_id.plan_line_executed_id
        if not current_line:
            return "ec", PLAN_LINE_NOT_ASSIGNED, None

        # Default values
        exit_code = command_log.command_status
        server = command_log.server_id
        server_variable_values = server.variable_value_ids

        # Check line condition
        if not current_line._is_executable_line(server):
            # Immediately return to the next line if condition fails
            return self._get_next_action_state(
                "n", PLAN_LINE_CONDITION_CHECK_FAILED, current_line
            )

        # Check plan action lines
        for action_line in current_line.action_ids:
            conditional_expression = (
                f"{exit_code} {action_line.condition} {action_line.value_char}"
            )
            # Evaluate expression using safe_eval
            if expr_eval(conditional_expression):
                action = action_line.action
                # Use custom exit code if action requires it
                if action == "ec" and action_line.custom_exit_code:
                    exit_code = action_line.custom_exit_code

                variable_value_obj = self.env["cx.tower.variable.value"]
                for variable_value in action_line.variable_value_ids:
                    variable = variable_value.variable_id
                    server_variable_value = server_variable_values.filtered(
                        lambda rec, variable=variable: rec.variable_id == variable,
                    )
                    if server_variable_value:
                        # update exist server value
                        server_variable_value.value_char = variable_value.value_char
                    else:
                        # create new value
                        variable_value_obj.create(
                            {
                                "variable_id": variable.id,
                                "value_char": variable_value.value_char,
                                "server_id": server.id,
                            }
                        )

                return self._get_next_action_state(action, exit_code, current_line)

        # If no action matched, fallback to default ones
        return self._get_next_action_state(None, exit_code, current_line)

    def _get_next_action_state(self, action, exit_code, current_line):
        """
        Determine the next action, exit code, and next line based on the current state.
        """
        lines = current_line.plan_id.line_ids
        is_last_line = current_line == lines[-1]

        # If no conditions were met fallback to default ones
        if not action:
            action = "n" if exit_code == 0 else current_line.plan_id.on_error_action

            # Exit with custom code
            if action == "ec":
                exit_code = current_line.plan_id.custom_exit_code

        # Determine the next line if current is not the last one
        next_line = None
        if action == "n" and not is_last_line:
            next_line = lines[indexOf(lines, current_line) + 1]

        if is_last_line:
            action = "e"

        return action, exit_code, next_line

    def _run_next_action(self, command_log):
        """Run next action based on command execution result

        Args:
            command_log (cx.tower.command.log()): Command log record
        """
        self.ensure_one()
        action, exit_code, plan_line_id = self._get_next_action_values(command_log)
        plan_log = command_log.plan_log_id

        # Update log message
        if exit_code == PLAN_LINE_CONDITION_CHECK_FAILED:
            # save log exit code as success
            exit_code = 0

        # Execute next line
        if action == "n" and plan_line_id:
            server = command_log.server_id
            if plan_line_id._is_executable_line(server):
                plan_line_id._execute(server, plan_log)
            else:
                plan_line_id._skip(server, plan_log)

        # Exit
        if action in ["e", "ec"]:
            plan_log.finish(exit_code)

        # NB: we are not putting any fallback here in case
        # someone needs to inherit and extend this function

    @api.depends("line_ids.command_id.access_level", "access_level")
    def _compute_command_access_level(self):
        """Check if the access level of a command in the plan
        is higher than the plan's access level"""
        for record in self:
            commands = record.mapped("line_ids").mapped("command_id")
            # Retrieve all commands associated with the flight plan
            commands_with_higher_access = commands.filtered(
                lambda c, access_level=record.access_level: c.access_level
                > access_level
            )
            if commands_with_higher_access:
                command_names = ", ".join(commands_with_higher_access.mapped("name"))
                record.access_level_warn_msg = _(
                    "The access level of command(s) '%(command_names)s' included in the"
                    " current Flight plan is higher than the access level of the"
                    " Flight plan itself. Please ensure that you want to allow"
                    " those commands to be run anyway.",
                    command_names=command_names,
                )
            else:
                record.access_level_warn_msg = False

    def action_open_plan_logs(self):
        """
        Open current flight plan log records
        """
        action = self.env["ir.actions.actions"]._for_xml_id(
            "cetmix_tower_server.action_cx_tower_plan_log"
        )
        action["domain"] = [("plan_id", "=", self.id)]
        return action

    def copy(self, default=None):
        # Call the super method to handle basic duplication
        default = dict(default or {})
        new_plan = super().copy(default=default)

        # Duplicate the lines from the original plan
        for line in self.line_ids:
            new_line = line.copy(
                {
                    # assign the new plan to the duplicated line
                    "plan_id": new_plan.id,
                }
            )

            # Duplicate actions linked to the line
            for action in line.action_ids:
                new_action = action.with_context(reference_mixin_skip_copy=True).copy(
                    # link new actions to the new line
                    {"line_id": new_line.id, "name": line.name}
                )

                # Duplicate variable values linked to the action
                for variable_value in action.variable_value_ids:
                    variable_value.with_context(reference_mixin_skip_self=True).copy(
                        {"plan_line_action_id": new_action.id}
                    )

        return new_plan
