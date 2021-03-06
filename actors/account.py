#!/usr/bin/python
from datetime import datetime
from pykka import ActorRegistry
from base_actor import ChildActor
from base_actor import ParentActor
from base_actor import MessageHandlerWrapper
from utils import log
from utils.protocol_pb2 import LinkAccountRep
from utils.protocol_pb2 import LinkAccountResultCode
from utils.protocol_pb2 import LoginAccountRep
from utils.protocol_pb2 import LoginAccountResultCode
from utils.protocol_pb2 import OSType
from utils.protocol_pb2 import SignInRep
from utils.protocol_pb2 import SignInResultCode
from utils.protocol_pb2 import SignUpRep
from utils.protocol_pb2 import SignUpResultCode
from utils.protocol_pb2 import SignType
from utils.misc import update_latest_login_players
from models.player import DeviceLink
from models.player import Player
from models.player import Session
from configs.world import World
from stats import DeltaSignIn


def get_account():
    account = ActorRegistry.get_by_class(Account)
    return account and account[0] or Account.start()


class BaseAuth(object):

    @property
    def player_index(self):
        raise NotImplementedError

    def get_player_by_pip(self, val):
        data = Player.load_by_attribute(self.player_index, val)
        return data and data[0]

    def get_player_by_device(self, device_id):
        device = DeviceLink(device_id=device_id).load()
        player_id = device.player_id
        if player_id:
            player = Player(id=player_id)
            player.load()
            return player
        return

    def generate_session(self, player_id):
        session = Session(player_id=player_id)
        session.store()
        session.refresh_session()
        return session

    def get_pip_id(self, msg):
        return msg.pip_id

    def init_player(self, msg):
        # TODO - verify with 3rd party?
        kwargs = {"is_new": True,
                  "name": msg.name,
                  self.player_index: self.get_pip_id(msg)}
        player = Player(**kwargs)
        player.store()
        log.info("Create player with basic info: %s" % str(kwargs))
        return player


class BaseSignUp(BaseAuth):

    def handle_signup(self, msg):
        resp = SignUpRep()
        # duplicate verify.
        if self.get_player_by_pip(msg.account):
            resp.result_code = SignUpResultCode.Value("DUPLICATE_ACCOUNT_ID")
            return resp
        self.init_player(msg)
        resp.result_code = SignUpResultCode.Value("SIGNUP_SUCCESS")
        return resp

    def get_pip_id(self, msg):
        return msg.account


class BaseSignIn(BaseAuth):

    def handle_signin(self, msg):
        resp = SignInRep()
        player = self.get_player_by_pip(msg.account)
        # check whether player exist.
        if not player:
            resp.result_code = SignInResultCode.Value("PLAYER_NOT_EXIST")
            return resp
        session = Session.load_by_attribute("player_id", player.id)
        session = session and session[0]
        if session:
            session.delete()
        session = self.generate_session(player.id)
        update_latest_login_players(player.id)

        resp.session_id = session.id
        resp.result_code = SignInResultCode.Value("SIGNIN_SUCCESS")
        resp.player_info.CopyFrom(World.GetPlayerInfo(player))
        return resp


class BaseLogin(BaseAuth):
    """
    Login account.

    Possible Login cases:
        Only device_id
            1. There's no account with this device
                ==> Create account and login.
            2. There's account with this device
                ==> Do login.
        Device_id + PIP
            1. There's no account with this device and no account with the PIP
                ==> Create account and login.
            2. Device_id and PIP using same account
                ==> Do login.
            3. There's account with this device but no account with the PIP
                ==> Create new account for the PIP, and redirect device_id to
                    the new account.
            4. There's account with this PIP
                ==> Always make the device point to the PIP's account.

    Note:
    If there's pre_device_id
        1. There's no account with pre_device_id -> Ignore
        2. Otherwise -> update pre_device_id with device_id in DB.
    """

    def update_old_device_id(self, msg):
        if msg.pre_device_id and msg.pre_device_id != msg.device_id:
            device = DeviceLink(device_id=msg.pre_device_id)
            if device.exist():
                device.load()
                device.device_id = msg.device_id
                device.store()
                log.info("Device %s updated to %s" %
                         (msg.pre_device_id, msg.device_id))

    def update_device_player_id(self, device_id, player_id):
        device = DeviceLink(device_id=device_id)
        device.load()
        log.info("Will redirect device(%s) from player(%s) to player(%s)" %
                 (device_id, device.player_id, player_id))
        device.player_id = player_id
        device.store()

    def init_device_link(self, device_id, player_id):
        device = DeviceLink(device_id=device_id, player_id=player_id)
        log.info("Device(%s) playing player(%s)" % (device_id, player_id))
        device.store()

    def handle_login(self, msg):
        request = msg.get('request')
        ip = request.remote_addr
        data = {'player': {}, 'client': {'ip_address': ip}}
        msg = msg.get('data')
        resp = LoginAccountRep()
        # update previous device_id to new device_id
        self.update_old_device_id(msg)
        device_player = self.get_player_by_device(msg.device_id)
        if device_player: log.info(device_player.id)
        if msg.pip_id:
            pip_player = self.get_player_by_pip(msg.pip_id)
            if device_player and device_player.exist():
                if pip_player and pip_player.exist():
                    player = pip_player
                    data['action'] = 'sign_in_device_pip'
                    if device_player.id != player.id:
                        # device not match pip player
                        # redirect device to pip player.
                        self.update_device_player_id(msg.device_id, player.id)
                        data['action'] = 'switch_device'
                else:
                    # create new account for pip and link device to it
                    player = self.init_player(msg)
                    self.update_device_player_id(msg.device_id, player.id)
                    data['action'] = 'switch_account'
            else:
                if pip_player and pip_player.exist():
                    player = pip_player
                    data['action'] = 'sign_in_pip'
                else:
                    # create new account
                    player = self.init_player(msg)
                    data['action'] = 'sign_up_pip'
                self.init_device_link(msg.device_id, player.id)
            self.generate_session(player.id)
        else:
            player = device_player
            data['action'] = 'sign_in_device'
            if not (player and player.exist()):
                # create new account
                player = self.init_player(msg)
                self.init_device_link(msg.device_id, player.id)
                data['action'] = 'sign_up_device'

        player.login_time = datetime.now()
        if msg.info.os_type:
            player.os_type = msg.info.os_type
        player.store()
        session = Session.load_by_attribute("player_id", player.id)
        session = session and session[0]
        if session:
            # delete previous session - avoid duplicate login.
            session.delete()
        session = self.generate_session(player.id)
        update_latest_login_players(player.id)
        resp.session_id = session.id
        resp.player_info.CopyFrom(World.GetPlayerInfo(player))
        resp.result_code = LoginAccountResultCode.Value("LOGIN_ACC_SUCCESS")

        data['player']['session'] = session.id
        return resp, (DeltaSignIn, data, player, msg.client)


class BaseLinkAccount(BaseAuth):
    """
    Link PIP account to player data.

    Possible cases:
        1. There's no account with this device.
            ==> Return error code.
        2. Account with this device is same with account with the PIP
            ==> Do noting. Return result_code saying already linked.
        3. Account with this device has no PIP, and on account with the PIP
            ==> Do Link.
        4. Account's PIP with this device is different from PIP
            4.1 There's no account with the PIP
                ==> Return result_code: LINK_ACC_DIFFERENT_PIP_NEW_PIP.
            4.2 There's another account with the PIP
                ==> Return result_code: LINK_ACC_DIFFERENT_PIP
    """

    def handle_link(self, player_id, msg):
        device_player = self.get_player_by_device(msg.device_id)
        if device_player is None:
            # LINK_ACC_DEVICE_ID_NOT_EXIST
            return LinkAccountResultCode.Value("LINK_ACC_OTHER")
        if device_player.id != player_id:
            # LINK_ACC_PLAYER_NOT_MATCH
            return LinkAccountResultCode.Value("LINK_ACC_OTHER")

        player = self.get_player_by_pip(msg.pip_id)
        if player:
            if player.id == device_player.id:
                # LINK_ACC_ALREADY_LINKED
                return LinkAccountResultCode.Value("LINK_ACC_OTHER")
            else:
                return LinkAccountResultCode.Value("LINK_ACC_DIFFERENT_PIP")
        elif getattr(device_player, self.player_index):
            # LINK_ACC_DIFFERENT_PIP_NEW_PIP
            return LinkAccountResultCode.Value("LINK_ACC_DIFFERENT_PIP")
        else:
            # Do link -> LINK_ACC_SUCCESS
            setattr(device_player, self.player_index, msg.pip_id)
            device_player.store()
            log.info("Link %s(%s) to player(id:%s)" %
                     (self.player_index, msg.pip_id, device_player.id))
            return LinkAccountResultCode.Value("LINK_ACC_OTHER")


class BaseAGC(BaseAuth):

    @property
    def player_index(self):
        return "agc_id"


class AGCSignUp(BaseSignUp, BaseAGC):
    pass


class AGCSignIn(BaseSignIn, BaseAGC):
    pass


class AGCLogin(BaseLogin, BaseAGC):
    pass


class LinkAGC(BaseLinkAccount, BaseAGC):
    pass


class BaseGoogle(BaseAuth):

    @property
    def player_index(self):
        return "gc_id"


class GoogleSignUp(BaseSignUp, BaseGoogle):
    pass


class GoogleSignIn(BaseSignIn, BaseGoogle):
    pass


class GoogleLogin(BaseLogin, BaseGoogle):
    pass


class LinkGoogle(BaseLinkAccount, BaseGoogle):
    pass


class BaseFacebook(BaseAuth):

    @property
    def player_index(self):
        return "facebook_id"


class FacebookSignUp(BaseSignUp, BaseFacebook):
    pass


class FacebookSignIn(BaseSignIn, BaseFacebook):
    pass


class FacebookLogin(BaseLogin, BaseFacebook):
    pass


class LinkFacebook(BaseLinkAccount, BaseFacebook):
    pass


class BaseDevice(BaseAuth):

    @property
    def player_index(self):
        return "device_id"


class DeviceSignUp(BaseSignUp, BaseDevice):
    pass


class DeviceSignIn(BaseSignIn, BaseDevice):
    pass


class DeviceLogin(BaseLogin, BaseDevice):

    def get_pip_id(self, msg):
        return msg.device_id


class Account(ParentActor):
    signup_map = {SignType.Value("APPLE"): AGCSignUp(),
                  SignType.Value("GOOGLE"): GoogleSignUp(),
                  SignType.Value("FACEBOOK"): FacebookSignUp(),
                  SignType.Value("DEVICE"): DeviceSignUp(),
                  }
    signin_map = {SignType.Value("APPLE"): AGCSignIn(),
                  SignType.Value("GOOGLE"): GoogleSignIn(),
                  SignType.Value("FACEBOOK"): FacebookSignIn(),
                  SignType.Value("DEVICE"): DeviceSignIn(),
                  }
    login_map = {SignType.Value("APPLE"): AGCLogin(),
                 SignType.Value("GOOGLE"): GoogleLogin(),
                 # SignType.Value("FACEBOOK"): FacebookLogin(),
                 SignType.Value("DEVICE"): DeviceLogin(),
                 }

    def SignUp(self, msg):
        log.info('account signup receive %s' % msg)
        sign_type = msg.type
        if sign_type:
            resp = self.signup_map.get(msg.type).handle_signup(msg)
        else:
            resp = SignUpRep()
            resp.result_code = SignUpResultCode.Value("MISSING_SIGNUP_TYPE")
        return self.resp(resp)

    def SignIn(self, msg):
        log.info('account sign in receive %s' % msg)
        handler = self.signin_map.get(msg.type)
        if handler:
            resp = handler.handle_signin(msg)
        else:
            resp = SignInRep()
            resp.result_code = SignInResultCode.Value("MISSING_SIGN_IN_TYPE")
        return self.resp(resp)

    def LoginAccount(self, msg):
        data = msg.get('data')
        log.info("Login account receive %s" % data)
        handler = self.login_map.get(data.type)
        if handler:
            resp, event = handler.handle_login(msg)
            self.send_event(*event)
        else:
            resp = LoginAccountRep()
            if data.type in SignType.values():
                resp.result_code = LoginAccountResultCode.Value(
                    "LOGIN_ACC_DISABLED_SIGN_TYPE")
            else:
                resp.result_code = LoginAccountResultCode.Value(
                    "LOGIN_ACC_MISSING_SIGN_TYPE")
        return self.resp(resp)

class LinkAccount(ChildActor):
    link_acc_map = {SignType.Value("APPLE"): LinkAGC(),
                    SignType.Value("GOOGLE"): LinkGoogle(),
                    SignType.Value("FACEBOOK"): LinkFacebook(),
                    }

    @MessageHandlerWrapper(LinkAccountRep, LinkAccountResultCode.Value(
        "LINK_ACC_OTHER"))
    def LinkAccount(self, msg):
        log.info("Link account receive %s" % msg)
        resp = LinkAccountRep()
        link_handler = self.link_acc_map.get(msg.type)
        if link_handler and msg.device_id:
            resp.result_code = link_handler.handle_link(self.parent.pid, msg)
        else:
            resp.result_code = LinkAccountResultCode.Value("LINK_ACC_OTHER")
        return self.resp(resp)