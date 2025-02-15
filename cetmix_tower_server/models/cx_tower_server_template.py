# Copyright (C) 2024 Cetmix OÜ
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CxTowerServerTemplate(models.Model):
    """Server Template. Used to simplify server creation"""

    _name = "cx.tower.server.template"
    _inherit = ["cx.tower.reference.mixin", "mail.thread", "mail.activity.mixin"]
    _description = "Cetmix Tower Server Template"

    active = fields.Boolean(default=True)

    # --- Connection
    ssh_port = fields.Char(string="SSH port", default="22")
    ssh_username = fields.Char(string="SSH Username")
    ssh_password = fields.Char(string="SSH Password")
    ssh_key_id = fields.Many2one(
        comodel_name="cx.tower.key",
        string="SSH Private Key",
        domain=[("key_type", "=", "k")],
    )
    ssh_auth_mode = fields.Selection(
        string="SSH Auth Mode",
        selection=[
            ("p", "Password"),
            ("k", "Key"),
        ],
    )
    use_sudo = fields.Selection(
        string="Use sudo",
        selection=[("n", "Without password"), ("p", "With password")],
        help="Run commands using 'sudo'",
    )

    # --- Attributes
    color = fields.Integer(help="For better visualization in views")
    os_id = fields.Many2one(string="Operating System", comodel_name="cx.tower.os")
    tag_ids = fields.Many2many(
        comodel_name="cx.tower.tag",
        relation="cx_tower_server_template_tag_rel",
        column1="server_template_id",
        column2="tag_id",
        string="Tags",
    )

    # --- Variables
    # We are not using variable mixin because we don't need to parse values
    variable_value_ids = fields.One2many(
        string="Variable Values",
        comodel_name="cx.tower.variable.value",
        auto_join=True,
        inverse_name="server_template_id",
    )

    # --- Server logs
    server_log_ids = fields.One2many(
        comodel_name="cx.tower.server.log", inverse_name="server_template_id"
    )

    # --- Flight Plan
    flight_plan_id = fields.Many2one(
        "cx.tower.plan",
        help="This flight plan will be run upon server creation",
        domain="[('server_ids', '=', False)]",
    )

    # --- Created Servers
    server_ids = fields.One2many(
        comodel_name="cx.tower.server",
        inverse_name="server_template_id",
    )
    server_count = fields.Integer(
        compute="_compute_server_count",
    )

    # -- Other
    note = fields.Text()

    def _compute_server_count(self):
        """
        Compute total server counts created from the templates
        """
        for template in self:
            template.server_count = len(template.server_ids)

    def action_create_server(self):
        """
        Returns wizard action to create new server
        """
        self.ensure_one()
        context = self.env.context.copy()
        context.update(
            {
                "default_server_template_id": self.id,  # pylint: disable=no-member
                "default_color": self.color,
                "default_ssh_port": self.ssh_port,
                "default_ssh_username": self.ssh_username,
                "default_ssh_password": self.ssh_password,
                "default_ssh_key_id": self.ssh_key_id.id,
                "default_ssh_auth_mode": self.ssh_auth_mode,
            }
        )
        if self.variable_value_ids:
            context.update(
                {
                    "default_line_ids": [
                        (
                            0,
                            0,
                            {
                                "variable_id": line.variable_id.id,
                                "value_char": line.value_char,
                                "option_id": line.option_id.id,
                                "required": line.required,
                            },
                        )
                        for line in self.variable_value_ids
                    ]
                }
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Server"),
            "res_model": "cx.tower.server.template.create.wizard",
            "view_mode": "form",
            "view_type": "form",
            "target": "new",
            "context": context,
        }

    def action_open_servers(self):
        """
        Return action to open related servers
        """
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "cetmix_tower_server.action_cx_tower_server"
        )
        action.update(
            {
                "domain": [("server_template_id", "=", self.id)],  # pylint: disable=no-member
            }
        )
        return action

    @api.model
    def create_server_from_template(self, template_reference, server_name, **kwargs):
        """This is a wrapper function that is meant to be called
        when we need to create a server from specific server template

        Args:
            template_reference (Char): Server template reference
            server_name (Char): Name of the new server

        Kwargs:
            partner (res.partner(), optional): Partner this server belongs to.
            ipv4 (Char, optional): IP v4 address. Defaults to None.
            ipv6 (Char, optional): IP v6 address.
                Must be provided in case IP v4 is not. Defaults to None.
            ssh_password (Char, optional): SSH password. Defaults to None.
            ssh_private_key_value (Char, optional): SSH private key content.
                Defaults to None.
            ssh_private_key_value (cx.tower.key(), optional): SSH private key record.
                Defaults to None.
            configuration_variables (Dict, optional): Custom configuration variable.
                Following format is used:
                    `variable_reference`: `variable_value_char`
                    eg:
                    {'branch': 'prod', 'odoo_version': '16.0'}

        Returns:
            cx.tower.server: newly created server record
        """
        template = self.get_by_reference(template_reference)
        return template._create_new_server(server_name, **kwargs)

    def _create_new_server(self, name, **kwargs):
        """Creates a new server from template

        Args:
            name (Char): Name of the new server

        Kwargs:
            partner (res.partner(), optional): Partner this server belongs to.
            ipv4 (Char, optional): IP v4 address. Defaults to None.
            ipv6 (Char, optional): IP v6 address.
                Must be provided in case IP v4 is not. Defaults to None.
            ssh_password (Char, optional): SSH password. Defaults to None.
            ssh_private_key_value (Char, optional): SSH private key content.
                Defaults to None.
            ssh_private_key_value (cx.tower.key(), optional): SSH private key record.
                Defaults to None.
            configuration_variables (Dict, optional): Custom configuration variable.
                Following format is used:
                    `variable_reference`: `variable_value_char`
                    eg:
                    {'branch': 'prod', 'odoo_version': '16.0'}

        Returns:
            cx.tower.server: newly created server record
        """
        # Retrieving the passed variables
        configuration_variables = kwargs.get("configuration_variables", {})

        # We validate mandatory variables
        self._validate_required_variables(configuration_variables)

        servers = (
            self.env["cx.tower.server"]
            .with_context(skip_ssh_settings_check=True)
            .create(
                self._prepare_server_values(
                    name=name,
                    server_template_id=self.id,
                    **kwargs,  # pylint: disable=no-member
                ),
            )
        )

        for server in servers:
            logs = server.server_log_ids.filtered(lambda rec: rec.log_type == "file")
            for log in logs:
                log.file_id = log.file_template_id.create_file(
                    server=server, raise_if_exists=False
                ).id

            flight_plan = server.server_template_id.flight_plan_id
            if flight_plan:
                flight_plan.execute(server)

        return servers

    def _get_fields_tower_server(self):
        """
        Return field name list to read from template and create new server
        """
        return [
            "ssh_username",
            "ssh_password",
            "ssh_key_id",
            "ssh_auth_mode",
            "use_sudo",
            "color",
            "os_id",
            "tag_ids",
            "variable_value_ids",
            "server_log_ids",
        ]

    def _prepare_server_values(self, **kwargs):
        """
        Prepare the server values to create a new server based on
        the current template. It reads all fields from the template, copies them,
        and processes One2many fields to create new related records. Magic fields
        like 'id', concurrency fields, and audit fields are excluded from the copied
        data.

        Args:
            **kwargs: Additional values to update in the final server record.

        Returns:
            list: A list of dictionaries representing the values for the new server
                  records.
        """
        model_fields = self._fields
        field_o2m_type = fields.One2many

        # define the magic fields that should not be copied
        # (including ID and concurrency fields)
        MAGIC_FIELDS = models.MAGIC_COLUMNS + [self.CONCURRENCY_CHECK_FIELD]

        # read all values required to create a new server from the template
        vals_list = self.read(self._get_fields_tower_server(), load=False)

        # process each template record
        for values in vals_list:
            template = self.browse(values["id"])

            for field in values.keys():
                if isinstance(model_fields[field], field_o2m_type):
                    # get related records for One2many field
                    related_records = getattr(template, field)
                    new_records = []
                    # for each related record, read its data and prepare it for copying
                    for record in related_records:
                        record_data = {
                            k: v
                            for k, v in record.read(load=False)[0].items()
                            if k not in MAGIC_FIELDS
                        }
                        # set the inverse field (link back to the template)
                        # to False to unlink from the original template
                        record_data[model_fields[field].inverse_name] = False
                        new_records.append((0, 0, record_data))

                    values[field] = new_records

            # custom specific variable values
            configuration_variables = kwargs.pop("configuration_variables", None)
            line_ids_variables = kwargs.pop("line_ids_variables", None)
            if configuration_variables:
                # Validate required variables
                self._validate_required_variables(configuration_variables)

                variable_vals_list = []
                variable_obj = self.env["cx.tower.variable"]

                for (
                    variable_reference,
                    variable_value,
                ) in configuration_variables.items():
                    variable = variable_obj.search(
                        [("reference", "=", variable_reference)]
                    )
                    if not variable:
                        variable = variable_obj.create(
                            {
                                "name": variable_reference,
                            }
                        )
                    variable_option_id = variable_id = False

                    if not variable_value and line_ids_variables:
                        val_found = next(
                            (
                                v
                                for v in line_ids_variables.values()
                                if v.get("variable_reference") == variable_reference
                            ),
                            None,
                        )
                        if val_found:
                            variable_value = val_found.get("value_char")
                            variable_option_id = val_found.get("option_id", False)
                            variable_id = val_found.get("variable_id", False)

                    variable_vals_list.append(
                        (
                            0,
                            0,
                            {
                                "variable_id": variable.id or variable_id,
                                "value_char": variable_value or "",
                                "option_id": variable_option_id,
                            },
                        )
                    )

                # update or add variable values
                existing_variable_values = values.get("variable_value_ids", [])
                variable_id_to_index = {
                    cmd[2]["variable_id"]: idx
                    for idx, cmd in enumerate(existing_variable_values)
                    if cmd[0] == 0 and "variable_id" in cmd[2]
                }

                for new_command in variable_vals_list:
                    variable_id = new_command[2]["variable_id"]
                    if variable_id in variable_id_to_index:
                        idx = variable_id_to_index[variable_id]
                        # update exist command
                        existing_variable_values[idx] = new_command
                    else:
                        # add new command
                        existing_variable_values.append(new_command)

                values["variable_value_ids"] = existing_variable_values

            # remove the `id` field to ensure a new record is created
            # instead of updating the existing one
            del values["id"]
            # update the values with additional arguments from kwargs
            values.update(kwargs)

        return vals_list

    def _validate_required_variables(self, configuration_variables):
        """
        Validate that all required variables are present, not empty,
        and that no required variable is entirely missing from the configuration.

        Args:
            configuration_variables (dict): A dictionary of variable references
                                             and their values.

        Raises:
            ValidationError: If all required variables are
                            missing from the configuration,
                            or if any required variable is empty or missing.
        """
        required_variables = self.variable_value_ids.filtered("required")
        if not required_variables:
            return

        required_refs = [var.variable_reference for var in required_variables]
        config_refs = list(configuration_variables.keys())

        missing_variables = [ref for ref in required_refs if ref not in config_refs]
        empty_variables = [
            ref
            for ref in required_refs
            if ref in config_refs and not configuration_variables[ref]
        ]

        if not (missing_variables or empty_variables):
            return

        error_parts = [
            _("Please resolve the following issues with configuration variables:")
        ]

        if missing_variables:
            error_parts.append(
                _(
                    "  - Missing variables: %(variables)s",
                    variables=", ".join(missing_variables),
                )
            )

        if empty_variables:
            error_parts.append(
                _(
                    "  - Empty values for variables: %(variables)s",
                    variables=", ".join(empty_variables),
                )
            )

        raise ValidationError("\n".join(error_parts))

    def copy(self, default=None):
        """Duplicate the server template along with variable values and server logs."""
        default = dict(default or {})

        # Duplicate the server template itself
        new_template = super().copy(default)

        # Duplicate variable values
        for variable_value in self.variable_value_ids:
            variable_value.with_context(reference_mixin_skip_self=True).copy(
                {"server_template_id": new_template.id}
            )

        # Duplicate server logs
        for server_log in self.server_log_ids:
            server_log.copy({"server_template_id": new_template.id})

        return new_template
