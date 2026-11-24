"""Resolve native travel requests against the live shared plaza and player homes."""
from private_channels import HOME, selected
from private_friends import state
from proto import net_pb2 as pb
from server.scene_data import get_session


def destination(session, other, reply, *, require_friend):
    if other == session.player_id or not other:
        return pb.StatusCode_BAD_REQ
    if session.dungeon[0]:
        return pb.StatusCode_CANT_JOIN_PLAYER_CURRENT_SCENE_CHANNEL
    relation = state(session.player_id, other)
    reverse = state(other, session.player_id)
    if pb.FriendStatus_BLACK in (relation, reverse):
        return pb.StatusCode_FRIEND_BLACK
    if require_friend and (relation, reverse) != (pb.FriendStatus_FRIEND, pb.FriendStatus_FRIEND):
        return pb.StatusCode_FRIEND_NOT_EXIST
    target = next((peer for peer in get_session() if peer.player_id == other), None)
    if target is None:
        return pb.StatusCode_PLAYER_NOT_FOUND
    if target.dungeon[0] or target.scene_id != HOME:
        return pb.StatusCode_CANT_JOIN_PLAYER_CURRENT_SCENE_CHANNEL
    request = pb.ChangeSceneChannelReq(scene_id=target.scene_id, channel_label=target.channel_id)
    channel, status = selected(session, request)
    if status == pb.StatusCode_OK:
        reply.scene_id = target.scene_id
        reply.channel_id = channel
        if not require_friend:
            reply.channel_label = channel
    return status


def visit_friend(session, request, reply):
    return destination(session, request.friend_id, reply, require_friend=True)


def visit_player(session, request, reply):
    return destination(session, request.player_id, reply, require_friend=False)
