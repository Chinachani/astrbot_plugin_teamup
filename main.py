# -*- coding: utf-8 -*-
import json
import random
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register


@register(
    "astrbot_plugin_teamup",
    "Chinachani",
    "组队报名与随机分队插件",
    "1.0.4",
    ""
)
class TeamUpPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir("teamup")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.data_dir / "state.json"
        self.state: Dict[str, Any] = {
            "scopes": {},
            "nicknames": {},
        }
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.state.update(data)
        except Exception as exc:
            logger.error("teamup: load state failed: %s", exc)
            logger.error(traceback.format_exc())

    def _save_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("teamup: save state failed: %s", exc)
            logger.error(traceback.format_exc())

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[str]:
        msg = getattr(event, "message_obj", None)
        if msg is None:
            return None
        gid = getattr(msg, "group_id", None) or getattr(msg, "group", None)
        return str(gid) if gid else None

    def _parse_scope(self, tokens: List[str]) -> Tuple[str, List[str]]:
        scope = "本群"
        if tokens and tokens[-1] in {"本群", "跨群"}:
            scope = tokens.pop(-1)
        return scope, tokens

    def _get_scope_id(self, event: AstrMessageEvent, scope: str) -> Optional[str]:
        scope = scope.strip()
        if scope == "跨群":
            return "global"
        gid = self._get_group_id(event)
        if not gid:
            return None
        return f"group:{gid}"

    def _get_sender_id(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    def _get_sender_name(self, event: AstrMessageEvent) -> str:
        return str(event.get_sender_name())

    def _get_display_name(self, event: AstrMessageEvent, scope_id: str) -> str:
        uid = self._get_sender_id(event)
        nick = self.state.get("nicknames", {}).get(uid) or self._get_sender_name(event)
        if scope_id == "global":
            return f"{nick}({uid})"
        return nick

    def _is_group_admin(self, event: AstrMessageEvent) -> bool:
        msg = getattr(event, "message_obj", None)
        sender = getattr(msg, "sender", None) if msg else None
        if sender is None:
            return False
        if getattr(sender, "is_owner", False) or getattr(sender, "is_admin", False):
            return True
        role = getattr(sender, "role", "")
        if isinstance(role, str) and role.lower() in {"owner", "admin"}:
            return True
        permission = getattr(sender, "permission", "")
        if isinstance(permission, str) and permission.lower() in {"owner", "admin"}:
            return True
        return False

    def _is_super_admin(self, user_id: str) -> bool:
        admin_ids = self.config.get("admin_ids", []) or []
        admin_ids = [str(x) for x in admin_ids]
        return user_id in admin_ids

    def _has_admin_rights(self, event: AstrMessageEvent) -> bool:
        uid = self._get_sender_id(event)
        return self._is_super_admin(uid) or self._is_group_admin(event)

    def _get_scope_state(self, scope_id: str) -> Dict[str, Any]:
        scopes = self.state.setdefault("scopes", {})
        return scopes.setdefault(scope_id, {
            "sessions": {},
            "active": "",
        })

    def _ensure_unique(self, lst: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in lst:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    def _remove_from_all(self, session: Dict[str, Any], user_id: str) -> None:
        if user_id in session.get("free", []):
            session["free"] = [x for x in session["free"] if x != user_id]
        teams = session.get("teams", {})
        for tname, members in list(teams.items()):
            teams[tname] = [x for x in members if x != user_id]
            if not teams[tname]:
                del teams[tname]

    def _get_session(self, scope_state: Dict[str, Any], session_name: str = "") -> Optional[Dict[str, Any]]:
        sessions = scope_state.get("sessions", {})
        if session_name:
            return sessions.get(session_name)
        active = scope_state.get("active", "")
        if active and active in sessions:
            return sessions.get(active)
        if len(sessions) == 1:
            return next(iter(sessions.values()))
        return None

    def _get_session_name(self, scope_state: Dict[str, Any], session_name: str = "") -> Optional[str]:
        sessions = scope_state.get("sessions", {})
        if session_name:
            return session_name if session_name in sessions else None
        active = scope_state.get("active", "")
        if active and active in sessions:
            return active
        if len(sessions) == 1:
            return next(iter(sessions.keys()))
        return None

    def _create_session(self, scope_state: Dict[str, Any], name: str, team_size: int, creator: str) -> None:
        scope_state.setdefault("sessions", {})[name] = {
            "team_size": team_size,
            "teams": {},
            "free": [],
            "created_by": creator,
        }
        scope_state["active"] = name

    def _assign_free(self, session: Dict[str, Any]) -> None:
        team_size = int(session.get("team_size", 2))
        teams = session.setdefault("teams", {})
        free = session.get("free", [])
        random.shuffle(free)
        for name, members in list(teams.items()):
            if not free:
                break
            while len(members) < team_size and free:
                members.append(free.pop(0))
            teams[name] = members
        idx = 1
        while free:
            team_name = f"队伍{idx}"
            while team_name in teams:
                idx += 1
                team_name = f"队伍{idx}"
            teams[team_name] = free[:team_size]
            free = free[team_size:]
        session["free"] = free

    def _find_user_team(self, session: Dict[str, Any], user_id: str) -> Tuple[Optional[str], int]:
        teams = session.get("teams", {})
        for name, members in teams.items():
            if user_id in members:
                return name, len(members)
        return None, 0

    @filter.command("组队菜单")
    async def menu(self, event: AstrMessageEvent):
        text = (
            "组队报名菜单（主菜单）：\n"
            "【信息与查询】\n"
            "- /组队大厅 [本群|跨群]\n"
            "- /组队列表 [组队名] [本群|跨群]\n"
            "- /组队空缺 [组队名] [本群|跨群]\n"
            "- /组队我的 [组队名] [本群|跨群]\n"
            "- /组队昵称 <昵称>\n"
            "\n"
            "【报名与组队】\n"
            "- /组队建队 <队伍名> [组队名] [本群|跨群]\n"
            "- /组队加入 [队伍名] [组队名] [本群|跨群]\n"
            "- /组队退出 [组队名] [本群|跨群]\n"
            "- /组队随机 [组队名] [本群|跨群]\n"
            "\n"
            "【管理员】\n"
            "- /组队创建 <每队人数> <组队名> [本群|跨群]\n"
            "- /组队切换 <组队名> [本群|跨群]\n"
            "- /组队随机 全部 [组队名] [本群|跨群]\n"
            "- /组队重置 [组队名] [本群|跨群]"
        )
        return event.plain_result(text)

    @filter.command("组队昵称")
    async def set_nickname(self, event: AstrMessageEvent, nickname: str = ""):
        nickname = nickname.strip()
        if not nickname:
            return event.plain_result("用法：/组队昵称 <昵称>")
        if len(nickname) > 20:
            return event.plain_result("昵称过长，请控制在 20 字以内。")
        uid = self._get_sender_id(event)
        self.state.setdefault("nicknames", {})[uid] = nickname
        self._save_state()
        return event.plain_result(f"已设置昵称：{nickname}")

    @filter.command("组队创建")
    async def create_teamup(self, event: AstrMessageEvent, team_size: str = "", name: str = "", scope: str = "本群"):
        if not self._has_admin_rights(event):
            return event.plain_result("仅管理员可创建组队报名。")
        if not team_size.isdigit() or int(team_size) <= 0:
            return event.plain_result("用法：/组队创建 <每队人数> <组队名> [本群|跨群]")
        name = name.strip()
        if not name:
            return event.plain_result("用法：/组队创建 <每队人数> <组队名> [本群|跨群]")
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        if name in scope_state.get("sessions", {}):
            return event.plain_result("该组队名已存在，请换一个名字。")
        self._create_session(scope_state, name, int(team_size), self._get_sender_id(event))
        self._save_state()
        return event.plain_result(f"已创建组队：{name}，每队 {team_size} 人，范围：{scope}")

    @filter.command("组队切换")
    async def switch_teamup(self, event: AstrMessageEvent, name: str = "", scope: str = "本群"):
        name = name.strip()
        if not name:
            return event.plain_result("用法：/组队切换 <组队名> [本群|跨群]")
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        if name not in scope_state.get("sessions", {}):
            return event.plain_result("该组队不存在。")
        scope_state["active"] = name
        self._save_state()
        return event.plain_result(f"已切换当前组队：{name}")

    @filter.command("组队大厅")
    async def list_hall(self, event: AstrMessageEvent, scope: str = "本群"):
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        sessions = scope_state.get("sessions", {})
        if not sessions:
            return event.plain_result("当前没有组队信息。")
        lines = [f"组队大厅（{scope}）："]
        for name, sess in sessions.items():
            team_size = sess.get("team_size", 2)
            teams = sess.get("teams", {})
            free = sess.get("free", [])
            lines.append(f"- {name}（每队{team_size}人，队伍{len(teams)}，自由{len(free)}）")
        return event.plain_result("\n".join(lines))

    @filter.command("组队加入")
    async def join_team(self, event: AstrMessageEvent, team_name: str = "", session_name: str = "", scope: str = "本群"):
        team_name = team_name.strip()
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        uid = self._get_sender_id(event)
        self._remove_from_all(session, uid)
        if team_name:
            teams = session.setdefault("teams", {})
            teams.setdefault(team_name, [])
            teams[team_name].append(uid)
            teams[team_name] = self._ensure_unique(teams[team_name])
            self._save_state()
            return event.plain_result(f"已加入队伍：{team_name}")
        session.setdefault("free", []).append(uid)
        session["free"] = self._ensure_unique(session["free"])
        self._save_state()
        return event.plain_result("已加入自由报名池（等待随机分队）")

    @filter.command("组队建队")
    async def create_team(self, event: AstrMessageEvent, team_name: str = "", session_name: str = "", scope: str = "本群"):
        team_name = team_name.strip()
        session_name = session_name.strip()
        if not team_name:
            return event.plain_result("用法：/组队建队 <队伍名> [组队名] [本群|跨群]")
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        uid = self._get_sender_id(event)
        self._remove_from_all(session, uid)
        teams = session.setdefault("teams", {})
        teams.setdefault(team_name, [])
        teams[team_name].append(uid)
        teams[team_name] = self._ensure_unique(teams[team_name])
        self._save_state()
        return event.plain_result(f"已创建并加入队伍：{team_name}")

    @filter.command("组队退出")
    async def leave_team(self, event: AstrMessageEvent, session_name: str = "", scope: str = "本群"):
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        uid = self._get_sender_id(event)
        self._remove_from_all(session, uid)
        self._save_state()
        return event.plain_result("已退出当前组队报名。")

    @filter.command("组队列表")
    async def list_teams(self, event: AstrMessageEvent, session_name: str = "", scope: str = "本群"):
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session_name = self._get_session_name(scope_state, session_name) or ""
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        teams = session.get("teams", {})
        free = session.get("free", [])
        lines = [f"组队：{session_name}", f"每队人数：{session.get('team_size', 2)}"]
        if teams:
            lines.append("队伍列表：")
            for name, members in teams.items():
                display = [self._get_display_name(event, scope_id) if uid == self._get_sender_id(event) else self.state.get("nicknames", {}).get(uid, uid) for uid in members]
                if scope_id == "global":
                    display = [self.state.get("nicknames", {}).get(uid, uid) + f"({uid})" for uid in members]
                lines.append(f"- {name} ({len(members)})：" + ", ".join(display))
        if free:
            display = [self.state.get("nicknames", {}).get(uid, uid) for uid in free]
            if scope_id == "global":
                display = [self.state.get("nicknames", {}).get(uid, uid) + f"({uid})" for uid in free]
            lines.append("自由报名：" + ", ".join(display))
        if not teams and not free:
            lines.append("暂无报名信息。")
        return event.plain_result("\n".join(lines))

    @filter.command("组队我的")
    async def my_team(self, event: AstrMessageEvent, session_name: str = "", scope: str = "本群"):
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        uid = self._get_sender_id(event)
        team_name, size = self._find_user_team(session, uid)
        if team_name:
            return event.plain_result(f"你在队伍：{team_name}（当前人数 {size}）")
        if uid in session.get("free", []):
            return event.plain_result("你在自由报名池（等待随机分队）。")
        return event.plain_result("你未加入当前组队。")

    @filter.command("组队空缺")
    async def list_vacancy(self, event: AstrMessageEvent, session_name: str = "", scope: str = "本群"):
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        team_size = int(session.get("team_size", 2))
        teams = session.get("teams", {})
        lines = []
        for name, members in teams.items():
            lack = max(team_size - len(members), 0)
            if lack > 0:
                lines.append(f"- {name} 缺 {lack} 人")
        if not lines:
            return event.plain_result("当前没有空缺队伍。")
        return event.plain_result("\n".join(lines))

    @filter.command("组队随机")
    async def random_assign(self, event: AstrMessageEvent, mode: str = "", session_name: str = "", scope: str = "本群"):
        mode = mode.strip()
        session_name = session_name.strip()
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        session = self._get_session(scope_state, session_name)
        if not session:
            return event.plain_result("未找到组队，请用 /组队大厅 查看并指定组队名。")
        team_size = int(session.get("team_size", 2))

        if mode == "全部":
            if not self._has_admin_rights(event):
                return event.plain_result("仅管理员可进行全员随机分队。")
            teams = session.setdefault("teams", {})
            free = session.get("free", [])
            all_members = []
            for m in teams.values():
                all_members.extend(m)
            all_members.extend(free)
            random.shuffle(all_members)
            teams.clear()
            idx = 1
            while all_members:
                team_name = f"队伍{idx}"
                teams[team_name] = all_members[:team_size]
                all_members = all_members[team_size:]
                idx += 1
            session["free"] = []
            self._save_state()
            return event.plain_result("已完成全员随机分队。")

        uid = self._get_sender_id(event)
        team_name, size = self._find_user_team(session, uid)
        if team_name and size > 1:
            return event.plain_result("你已在队伍中，若要随机请先退出队伍。")
        # member random: move to free then assign only free
        self._remove_from_all(session, uid)
        session.setdefault("free", []).append(uid)
        session["free"] = self._ensure_unique(session["free"])
        self._assign_free(session)
        self._save_state()
        return event.plain_result("已为你进行随机分队。")

    @filter.command("组队重置")
    async def reset_teamup(self, event: AstrMessageEvent, session_name: str = "", scope: str = "本群"):
        session_name = session_name.strip()
        if not self._has_admin_rights(event):
            return event.plain_result("仅管理员可重置。")
        scope_id = self._get_scope_id(event, scope)
        if not scope_id:
            return event.plain_result("跨群请写“跨群”，群内请在群聊使用。")
        scope_state = self._get_scope_state(scope_id)
        if session_name:
            scope_state.get("sessions", {}).pop(session_name, None)
        else:
            active = scope_state.get("active", "")
            if active:
                scope_state.get("sessions", {}).pop(active, None)
        self._save_state()
        return event.plain_result("已重置该范围的组队报名。")


class Main(TeamUpPlugin):
    """兼容旧版加载器"""
