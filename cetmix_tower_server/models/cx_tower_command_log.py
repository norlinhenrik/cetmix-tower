# Copyright (C) 2022 Cetmix OÜ
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import fields, models


class CxTowerCommandLog(models.Model):
    _name = "cx.tower.command.log"
    _description = "Cetmix Tower Command Log"
    _order = "start_date desc, id desc"

    name = fields.Char(compute="_compute_name", compute_sudo=True)
    label = fields.Char(help="Custom label. Can be used for search/tracking")
    server_id = fields.Many2one(comodel_name="cx.tower.server")

    # -- Time
    start_date = fields.Datetime(string="Started")
    finish_date = fields.Datetime(string="Finished")
    duration = fields.Float(
        string="Duration, sec", help="Time consumed for execution, seconds"
    )

    # -- Command
    is_running = fields.Boolean(help="Command is being executed right now")
    command_id = fields.Many2one(comodel_name="cx.tower.command")
    code = fields.Text(string="Command Code")
    command_status = fields.Integer(string="Exit Code")
    command_response = fields.Text(string="Response")
    command_error = fields.Text(string="Error")
    use_sudo = fields.Selection(
        string="Use sudo",
        selection=[("n", "Without password"), ("p", "With password")],
        help="Run commands using 'sudo'",
    )

    # -- Flightplan
    plan_log_id = fields.Many2one(comodel_name="cx.tower.plan.log")

    def _compute_name(self):
        for rec in self:
            rec.name = ": ".join((rec.server_id.name, rec.command_id.name))  # type: ignore

    def start(self, server_id, command_id, start_date=None, **kwargs):
        """Creates initial log record when command is started

        Args:
            server_id (int) id of the server.
            command_id (int) id of the command.
            start_date (datetime) command start date time.
            **kwargs (dict): optional values
        Returns:
            (cx.tower.command.log()) new command log record or False
        """
        vals = {
            "server_id": server_id,
            "command_id": command_id,
            "is_running": True,
            "start_date": start_date if start_date else fields.Datetime.now(),
        }
        # Apply kwargs
        vals.update(kwargs)
        log_record = self.sudo().create(vals)
        return log_record

    def finish(
        self, finish_date=None, status=None, response=None, error=None, **kwargs
    ):
        """Save final command result when command is finished

        Args:
            log_record (cx.tower.command.log()): Log record
            finish_date (Datetime): command finish date time.
            **kwargs (dict): optional values
        """
        now = fields.Datetime.now()

        # Compose response message
        command_response = ""
        if response:
            response_vals = [r for r in response]
            command_response = (
                "".join(response_vals) if len(response_vals) > 1 else response_vals[0]
            )

        # Compose error message
        command_error = ""
        if error:
            error_vals = [e for e in error]
            command_error = (
                "".join(error_vals) if len(error_vals) > 1 else error_vals[0]
            )

        for rec in self.sudo():
            # Duration
            date_finish = finish_date if finish_date else now
            duration = (date_finish - rec.start_date).total_seconds()  # type: ignore
            if duration < 0:
                duration = 0

            vals = {
                "is_running": False,
                "finish_date": date_finish,
                "duration": duration,
                "command_status": -1 if status is None else status,
                "command_response": command_response,
                "command_error": command_error,
            }
            # Apply kwargs and write
            vals.update(kwargs)
            rec.write(vals)

            # Trigger post finish hook
            rec._command_finished()

    def record(
        self,
        server_id,
        command_id,
        start_date,
        finish_date,
        status=0,
        response=None,
        error=None,
        **kwargs,
    ):
        """Record completed command directly without using start/stop

        Args:
            server_id (int) id of the server.
            command_id (int) id of the command.
            start_date (datetime) command start date time.
            finish_date (datetime) command finish date time.
            status (int, optional): command execution status. Defaults to 0.
            response (list, optional): SSH response. Defaults to None.
            error (list, optional): SSH error. Defaults to None.
            **kwargs (dict): values to store
        Returns:
            (cx.tower.command.log()) new command log record
        """

        # Compute duration
        duration = (finish_date - start_date).total_seconds()
        if duration < 0:
            duration = 0

        # Compose response message
        command_response = ""
        if response:
            response_vals = [r for r in response]
            command_response = (
                "".join(response_vals) if len(response_vals) > 1 else response_vals[0]
            )

        # Compose error message
        command_error = ""
        if error:
            error_vals = [e for e in error]
            command_error = (
                "".join(error_vals) if len(error_vals) > 1 else error_vals[0]
            )

        vals = kwargs or {}
        vals.update(
            {
                "server_id": server_id,
                "command_id": command_id,
                "start_date": start_date,
                "finish_date": finish_date,
                "duration": duration,
                "command_status": status,
                "command_response": command_response,
                "command_error": command_error,
            }
        )
        rec = self.sudo().create(vals)
        rec._command_finished()
        return rec

    def _command_finished(self):
        """Triggered when command is finished
        Inherit to implement your own hooks
        """

        # Trigger next flightplan line
        for rec in self:
            if rec.plan_log_id:  # type: ignore
                rec.plan_log_id._plan_command_finished(rec)  # type: ignore
