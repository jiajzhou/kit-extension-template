import omni.usd
from datetime import datetime, timedelta
from omni.kit.scripting import BehaviorScript
import concurrent.futures
from pxr import Sdf, Gf
import carb.events

from .Main import get_state, get_executor
from .omni_utils import hex_to_vec_3
from .twinmaker_utils import date_to_iso, apply_operator
from .TwinMaker import TwinMaker, DataBinding, RuleExpression
from .constants import WORKSPACE_ATTR, ASSUME_ROLE_ATTR, ENTITY_ATTR, COMPONENT_ATTR, PROPERTY_ATTR, REGION_ATTR, RULE_OP_ATTR, RULE_VAL_ATTR, MAT_COLOR_ATTR, CHANGE_MAT_PATH

class ModelShader(BehaviorScript):
    def on_init(self):
        self.__init_attributes()
        
        self._running_time = 0

        self._twinmaker = TwinMaker(self._region, self._assume_role_arn, self._workspace_id)

        # Fetch property data type
        self._data_type = self._twinmaker.get_property_value_type(self._data_binding)

        # Set default values of the material
        self.__init_material_attributes()

        self._is_rule_matched = False
        self._changed_material = False

        carb.log_info(f"{__class__.__name__}.on_init()->{self.prim_path}")

    # Get attributes from prim with property data binding
    def __init_attributes(self):
        # Get shared attributes from Logic object
        logic_prim_path = '/World/Logic'
        stage = omni.usd.get_context().get_stage()
        logic_prim = stage.GetPrimAtPath(logic_prim_path)
        self._workspace_id = logic_prim.GetAttribute(WORKSPACE_ATTR).Get()
        self._assume_role_arn = logic_prim.GetAttribute(ASSUME_ROLE_ATTR).Get()
        self._region = logic_prim.GetAttribute(REGION_ATTR).Get()

        # Data binding attributes are on current prim
        entity_id = self.prim.GetAttribute(ENTITY_ATTR).Get()
        component_name = self.prim.GetAttribute(COMPONENT_ATTR).Get()
        property_name = self.prim.GetAttribute(PROPERTY_ATTR).Get()
        self._data_binding = DataBinding(entity_id, component_name, property_name)

        # Rule for data binding attributes: prop op value (e.g. status == "ACTIVE")
        rule_op = self.prim.GetAttribute(RULE_OP_ATTR).Get()
        rule_val = self.prim.GetAttribute(RULE_VAL_ATTR).Get()
        self._rule_expression = RuleExpression(property_name, rule_op, rule_val)
        
        # Material to change to when rule expression is true
        mat_color = self.prim.GetAttribute(MAT_COLOR_ATTR).Get()
        self._mat_color = None if mat_color == '' else hex_to_vec_3(mat_color)
        change_mat_path = self.prim.GetAttribute(CHANGE_MAT_PATH).Get()
        self._change_mat_path = None if change_mat_path == '' else change_mat_path

    def __init_material_attributes(self):
        stage = omni.usd.get_context().get_stage()
        # Get material
        material_targets = self.prim.GetRelationship('material:binding').GetTargets()
        if len(material_targets) > 0:
            prim_material_path = material_targets[0]
            shader_path = f'{prim_material_path}/Shader'
            self._shader_prim = stage.GetPrimAtPath(shader_path)
            self._default_tint_color = self._shader_prim.GetAttribute('inputs:diffuse_tint').Get()
            self._default_albedo_add = self._shader_prim.GetAttribute('inputs:albedo_add').Get()
            self._default_material = prim_material_path
        
    def update_shader(self, tint_color, albedo_add, material_path):
        try:
            if tint_color is not None:
                self._shader_prim.GetAttribute('inputs:diffuse_tint').Set(tint_color)
                self._shader_prim.GetAttribute('inputs:albedo_add').Set(albedo_add)
            elif material_path is not None:
                omni.kit.commands.execute(
                    "BindMaterialCommand",
                    prim_path=self.prim_path,
                    material_path=Sdf.Path(material_path),
                    strength=['strongerThanDescendants']
                )
        except:
            pass
    
    # Rule color must be set as attributes on the object
    # Specify whether the rule is matched and the material should change
    def change_material(self, should_change):
        if should_change:
            self.update_shader(self._mat_color, 0.5, self._change_mat_path)
        else:
            self.update_shader(self._default_tint_color, self._default_albedo_add, self._default_material)

    def is_prim_selected(self):
        return self.selection.is_prim_path_selected(self.prim_path.__str__())
    
    def match_rule(self, property_value):
        is_rule_matched = False
        if property_value is not None:
            converted_prop_value = str(property_value) if self._data_type is 'stringValue' else float(property_value)
            converted_rule_val = str(self._rule_expression.rule_val) if self._data_type is 'stringValue' else float(self._rule_expression.rule_val)
            is_rule_matched = apply_operator(converted_prop_value, self._rule_expression.rule_op, converted_rule_val)
        return is_rule_matched

    # def on_destroy(self):
        # print(f"{__class__.__name__}.on_destroy()->{self.prim_path}")

    # def on_play(self):
        # print(f"{__class__.__name__}.on_play()->{self.prim_path}")

    def on_pause(self):
        self._running_time = 0
        # print(f"{__class__.__name__}.on_pause()->{self.prim_path}")

    def on_stop(self):
        self._running_time = 0
        self.change_material(False)
        # print(f"{__class__.__name__}.on_stop()->{self.prim_path}")

    def on_update(self, current_time: float, delta_time: float):
        state = get_state()
        executor = get_executor()
        # Fetch data approx every 10 seconds
        frequency = 10
        
        # This script is sometimes initialized before Main.py
        if state is None:
            carb.log_info('ModelShader state is not defined')
            return

        if state.is_play:
            # Math is finicky
            if round(self._running_time % frequency, 2) - 0.015 < 0:
                end_time = datetime.now()
                start_time = end_time - timedelta(minutes=1)
                processes = []
                # Fetch alarm status in background process
                future = executor.submit(self._twinmaker.get_latest_property_value, self._data_binding, self._data_type, date_to_iso(start_time), date_to_iso(end_time))
                processes.append(future)
                for _ in concurrent.futures.as_completed(processes):
                    result = _.result()
                    is_rule_matched = self.match_rule(result)
                    self.change_material(is_rule_matched)
            self._running_time = self._running_time + delta_time
        else:
            self.change_material(False)