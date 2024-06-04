# Tildagon port by Richard Birkby
# Inspired by the Arduino Microview port by Richard Birkby - https://github.com/rbirkby/ArduinoTetris
# Inspired by the Raspberry Pi Pico port by Richard Birkby - https://github.com/rbirkby/picotetris
# Original JavaScript implementation - Jake Gordon - https://github.com/jakesgordon/javascript-tetris
# MIT licenced

import random
import app
from app_components import clear_background, Notification
from events.input import Buttons, BUTTON_TYPES
from system.eventbus import eventbus
from system.scheduler.events import RequestForegroundPushEvent
from app_components.tokens import display_x, display_y


class TildagonTetris(app.App):

    ###########################################################################
    # base helper methods
    ###########################################################################

    def random(self, min, max):
        return min + (random() * (max - min))

    ################
    # Game constants
    ################
    DIR = {"UP": 0, "RIGHT": 1, "DOWN": 2, "LEFT": 3, "MIN": 0, "MAX": 3}
    speed = {"start": 0.6, "decrement": 0.005, "min": 0.1}
    nx = 12  # width of tetris court (in blocks)
    ny = 15  # height of tetris court (in blocks)
    nu = 3  # width/height of upcoming preview (in blocks)

    ###########################################
    # game variables (initialized during reset)
    ###########################################
    dx = (0.6 * display_x) / nx  # pixel size of a single tetris block
    dy = (0.75 * display_y) / ny
    blocks = (
        []
    )  # 2 dimensional array (nx*ny) representing tetris court - either empty block or occupied by a 'piece'
    actions = []  # queue of user actions (inputs)
    playing = True  # true|false - game is in progress
    dt = 0  # time since starting this game
    current = None  # the current and next piece
    next_piece = None
    score = 0  # the current score
    vscore = 0  # the currently displayed score (it catches up to score in small chunks - like a spinning slot machine)
    rows = 0  # number of completed rows in the current game
    step = 0  # how long before current piece drops by 1 row
    lost = False
    notification = None

    ###########################################################################
    # tetris pieces
    #
    # blocks: each element represents a rotation of the piece (0, 90, 180, 270)
    #         each element is a 16 bit integer where the 16 bits represent
    #         a 4x4 set of blocks, e.g. j.blocks[0] = 0x44C0
    #
    #             0100 = 0x4 << 3 = 0x4000
    #             0100 = 0x4 << 2 = 0x0400
    #             1100 = 0xC << 1 = 0x00C0
    #             0000 = 0x0 << 0 = 0x0000
    #                               ------
    #                               0x44C0
    #
    ###########################################################################

    i = {
        "size": 4,
        "blocks": [0x0F00, 0x2222, 0x00F0, 0x4444],
        "color": {"r": 0, "g": 255, "b": 255},
    }
    j = {
        "size": 3,
        "blocks": [0x44C0, 0x8E00, 0x6440, 0x0E20],
        "color": {"r": 0, "g": 0, "b": 255},
    }
    l = {
        "size": 3,
        "blocks": [0x4460, 0x0E80, 0xC440, 0x2E00],
        "color": {"r": 255, "g": 165, "b": 0},
    }
    o = {
        "size": 2,
        "blocks": [0xCC00, 0xCC00, 0xCC00, 0xCC00],
        "color": {"r": 255, "g": 255, "b": 0},
    }
    s = {
        "size": 3,
        "blocks": [0x06C0, 0x8C40, 0x6C00, 0x4620],
        "color": {"r": 0, "g": 255, "b": 0},
    }
    t = {
        "size": 3,
        "blocks": [0x0E40, 0x4C40, 0x4E00, 0x4640],
        "color": {"r": 128, "g": 0, "b": 128},
    }
    z = {
        "size": 3,
        "blocks": [0x0C60, 0x4C80, 0xC600, 0x2640],
        "color": {"r": 255, "g": 0, "b": 0},
    }

    ##################################################
    # do the bit manipulation and iterate through each
    # occupied block (x,y) for a given piece
    ##################################################

    def eachblock(self, type, x, y, dir, fn):
        blocks = type["blocks"][dir]
        bit = 0x8000
        row = 0
        col = 0
        while bit > 0:
            if blocks & bit:
                fn(x + col, y + row)
            bit = bit >> 1
            col += 1
            if col == 4:
                col = 0
                row += 1

    ######################################################
    # check if a piece can fit into a position in the grid
    ######################################################

    def occupied(self, type, x, y, dir):
        result = False

        def isOccupied(x, y):
            if (
                (x < 0)
                or (x >= self.nx)
                or (y < 0)
                or (y >= self.ny)
                or self.getBlock(x, y)
            ):
                nonlocal result
                result = True

        self.eachblock(type, x, y, dir, isOccupied)
        return result

    def unoccupied(self, type, x, y, dir):
        return not self.occupied(type, x, y, dir)

    ##########################################
    # start with 4 instances of each piece and
    # pick randomly until the 'bag is empty'
    ##########################################

    def randomPiece(self):
        pieces = [
            self.i,
            self.i,
            self.i,
            self.i,
            self.j,
            self.j,
            self.j,
            self.j,
            self.l,
            self.l,
            self.l,
            self.l,
            self.o,
            self.o,
            self.o,
            self.o,
            self.s,
            self.s,
            self.s,
            self.s,
            self.t,
            self.t,
            self.t,
            self.t,
            self.z,
            self.z,
            self.z,
            self.z,
        ]
        piece = random.choice(pieces)
        return {
            "type": piece,
            "dir": self.DIR["UP"],
            "x": random.randint(0, self.nx - piece["size"]),
            "y": 0,
        }

    ##################################
    # GAME LOOP
    ##################################

    def __init__(self):
        # Need to call to access overlays
        super().__init__()

        eventbus.on(RequestForegroundPushEvent, self.handle_foregroundpush, self)
        self.button_states = Buttons(self)
        self.reset()  # reset the per-game variables
        self.play()

    def handle_foregroundpush(self, event: RequestForegroundPushEvent):
        if event.app == self:
            self.reset()  # reset the per-game variables
            self.play()

    def update(self, delta):
        if self.button_states.get(BUTTON_TYPES["CANCEL"]):
            self.lose()
            self.button_states.clear()
            self.minimise()
        elif self.playing:
            if self.button_states.get(BUTTON_TYPES["LEFT"]):
                self.actions.append(self.DIR["LEFT"])
            elif self.button_states.get(BUTTON_TYPES["RIGHT"]):
                self.actions.append(self.DIR["RIGHT"])
            elif self.button_states.get(BUTTON_TYPES["UP"]):
                self.actions.append(self.DIR["UP"])
                self.button_states.clear()
            elif self.button_states.get(BUTTON_TYPES["DOWN"]):
                self.actions.append(self.DIR["DOWN"])

        if self.playing:
            if self.vscore < self.score:
                self.setVisualScore(self.vscore + 1)
            action = self.actions.pop(0) if self.actions else None
            self.handle(action)
            self.dt += delta
            if self.dt > self.step:
                self.dt -= self.step
                self.drop()

        if self.lost:
            self.notification = Notification("GameOver")
            self.notification.update(delta)
            self.lost = False

    ##################################
    # GAME LOGIC
    ##################################

    def play(self):
        self.playing = True

    def lose(self):
        self.playing = False
        self.lost = True

    def setVisualScore(self, n=None):
        self.vscore = n if n is not None else self.score

    def setScore(self, n):
        self.score = n
        self.setVisualScore(n)

    def addScore(self, n):
        self.score += n

    def clearScore(self):
        self.setScore(0)

    def clearRows(self):
        self.setRows(0)

    def setRows(self, n):
        self.rows = n
        # self.step = max(self.speed['min'], self.speed['start'] - (self.speed['decrement'] * self.rows))
        self.step = 500

    def addRows(self, n):
        self.setRows(self.rows + n)

    def getBlock(self, x, y):
        if self.blocks and x < len(self.blocks) and y < len(self.blocks[x]):
            return self.blocks[x][y]
        return None

    def setBlock(self, x, y, type):
        if x >= len(self.blocks):
            self.blocks.extend([[] for _ in range(x - len(self.blocks) + 1)])
        if y >= len(self.blocks[x]):
            self.blocks[x].extend([None for _ in range(y - len(self.blocks[x]) + 1)])
        self.blocks[x][y] = type

    def clearBlocks(self):
        self.blocks.clear()

    def clearActions(self):
        self.actions.clear()

    def setCurrentPiece(self, piece):
        self.current = piece

    def setNextPiece(self, piece):
        self.next_piece = piece

    def reset(self):
        self.dt = 0
        self.clearActions()
        self.clearBlocks()
        self.clearRows()
        self.clearScore()
        self.setCurrentPiece(self.randomPiece())
        self.setNextPiece(self.randomPiece())
        self.lost = False
        self.notification = None

    def handle(self, action):
        if action == self.DIR["LEFT"]:
            self.move(self.DIR["LEFT"])
        elif action == self.DIR["RIGHT"]:
            self.move(self.DIR["RIGHT"])
        elif action == self.DIR["UP"]:
            self.rotate()
        elif action == self.DIR["DOWN"]:
            self.drop()

    def move(self, dir):
        x = self.current["x"]
        y = self.current["y"]
        if dir == self.DIR["RIGHT"]:
            x += 1
        elif dir == self.DIR["LEFT"]:
            x -= 1
        elif dir == self.DIR["DOWN"]:
            y += 1
        if self.unoccupied(self.current["type"], x, y, self.current["dir"]):
            self.current["x"] = x
            self.current["y"] = y
            return True

        return False

    def rotate(self):
        newdir = (
            self.DIR["MIN"]
            if self.current["dir"] == self.DIR["MAX"]
            else self.current["dir"] + 1
        )
        if self.unoccupied(
            self.current["type"], self.current["x"], self.current["y"], newdir
        ):
            self.current["dir"] = newdir

    def drop(self):
        if not self.move(self.DIR["DOWN"]):
            self.addScore(10)
            self.dropPiece()
            self.removeLines()
            self.setCurrentPiece(self.next_piece)
            self.setNextPiece(self.randomPiece())
            self.clearActions()
            if self.occupied(
                self.current["type"],
                self.current["x"],
                self.current["y"],
                self.current["dir"],
            ):
                self.lose()

    def dropPiece(self):
        self.eachblock(
            self.current["type"],
            self.current["x"],
            self.current["y"],
            self.current["dir"],
            lambda x, y: self.setBlock(x, y, self.current["type"]),
        )

    def removeLines(self):
        n = 0
        for y in range(self.ny - 1, -1, -1):
            complete = True
            for x in range(self.nx):
                if not self.getBlock(x, y):
                    complete = False
                    break
            if complete:
                self.removeLine(y)
                y += 1
                n += 1
        if n > 0:
            self.addRows(n)
            self.addScore(100 * 2 ** (n - 1))

    def removeLine(self, n):
        for y in range(n, 0, -1):
            for x in range(self.nx):
                self.setBlock(x, y, self.getBlock(x, y - 1))

    ##################################
    # RENDERING
    ##################################

    def draw(self, ctx):
        clear_background(ctx)
        ctx.save()
        ctx.translate(-(0.6 * display_x) / 2, -(0.75 * display_y) / 2)

        self.drawCourt(ctx)
        self.drawNext(ctx)
        self.drawScore(ctx)
        self.drawRows(ctx)

        ctx.restore()

        if self.notification:
            self.notification.draw(ctx)

    def drawCourt(self, ctx):
        ctx.rgba(0, 255, 0, 0.3).rectangle(
            0, 0, self.nx * self.dx - 1, self.ny * self.dy - 1
        ).fill()

        if self.playing:
            self.drawPiece(
                ctx,
                self.current["type"],
                self.current["x"],
                self.current["y"],
                self.current["dir"],
            )
        for y in range(self.ny):
            for x in range(self.nx):
                block = self.getBlock(x, y)
                if block:
                    self.drawBlock(ctx, x, y, block["color"])

        ctx.rgb(0, 255, 0).rectangle(
            0, 0, self.nx * self.dx - 1, self.ny * self.dy - 1
        ).stroke()

    def drawNext(self, ctx):
        direction = (
            self.DIR["RIGHT"]
            if self.next_piece["type"] in (self.z, self.i, self.s, self.z, self.t)
            else self.DIR["UP"]
        )
        offset = -4 if self.next_piece["type"] in (self.i, self.l) else -3
        self.drawPiece(ctx, self.next_piece["type"], offset, 6, direction)

    def drawScore(self, ctx):
        width = ctx.text_width(str(self.score))
        ctx.rgb(255, 0, 0).move_to((0.6 * display_x - width) / 2, 203).text(
            str(self.score)
        )

    def drawRows(self, ctx):
        width = ctx.text_width(str(self.rows))
        ctx.rgb(255, 0, 0).move_to(0.7 * display_x - width / 2, 100).text(
            str(self.rows)
        )

    def drawPiece(self, ctx, type, x, y, dir):
        self.eachblock(
            type, x, y, dir, lambda x, y: self.drawBlock(ctx, x, y, type["color"])
        )

    def drawBlock(self, ctx, x, y, color):
        ctx.rgb(color["r"], color["g"], color["b"]).rectangle(
            x * self.dx, y * self.dy, self.dx, self.dy
        ).fill()
        ctx.rgb(0, 0, 0).rectangle(x * self.dx, y * self.dy, self.dx, self.dy).stroke()


__app_export__ = TildagonTetris
