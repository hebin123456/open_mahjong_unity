"""纯标准库二维码（QR Code）编码器，供单机服务 /host_info 生成局域网加入二维码。

设计约束：
- 只依赖标准库（Chaquopy 与随包 Python 都能直接用）；
- 只做字节模式（URL 为 ASCII），纠错级别固定 L，版本自动选 1–6（v7+ 需要额外的
  version info 块，产品场景用不到，超长直接报错）；
- 输出 0/1 模块矩阵，交给 C# 画成 Texture2D，不生成图片。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 版本参数（纠错级别 L）
# ---------------------------------------------------------------------------
# 每版本的数据码字数；v1–v5 单块，v6 拆 2 个等长块。
_DATA_CODEWORDS = {1: 19, 2: 34, 3: 55, 4: 80, 5: 108, 6: 136}
_BLOCKS = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2}
# 每版本的剩余位（编码区填 0 并参与掩码）。
_REMAINDER_BITS = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7}
# 校正图形中心坐标（只含不与定位图形重叠的组合）。
_ALIGNMENT_CENTERS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
}
# 字节模式下 v1–v6 的计数位宽均为 8。
_BYTE_MODE_INDICATOR = 0b0100
_ECC_LEVEL_L_BITS = 0b01
_FORMAT_MASK = 0x5412  # 与生成多项式结果异或的掩码
_FORMAT_GEN = 0x537    # BCH(15,5) 生成多项式

# ---------------------------------------------------------------------------
# GF(256) 与 Reed-Solomon
# ---------------------------------------------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(ecc_len: int) -> list:
    """(x - α^0)(x - α^1)...(x - α^(ecc_len-1)) 的系数（高次在前）。"""
    poly = [1]
    for i in range(ecc_len):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= _gf_mul(c, _EXP[i])
            nxt[j + 1] ^= c
        poly = nxt
    return poly


def _rs_ecc(data: bytes, ecc_len: int) -> bytes:
    # _rs_generator 返回低次在前；长除按「最高次系数在前」使用，取反即可
    gen = list(reversed(_rs_generator(ecc_len)))
    rem = [0] * (len(data) + ecc_len)
    for i, b in enumerate(data):
        rem[i] = b
    # 多项式长除（等价于逐字节反馈异或）
    for i in range(len(data)):
        factor = rem[i]
        if factor == 0:
            continue
        for j in range(1, len(gen)):
            rem[i + j] ^= _gf_mul(gen[j], factor)
    return bytes(rem[len(data):])


# ---------------------------------------------------------------------------
# 编码
# ---------------------------------------------------------------------------
def _bit_buffer_bytes(data: bytes, data_len: int, remainder_bits: int) -> list:
    """字节模式序列：模式 4bit + 计数 8bit + 数据，补终结符/对齐位/填充字节。"""
    bits = [(_BYTE_MODE_INDICATOR >> s) & 1 for s in range(3, -1, -1)]
    bits.extend([(len(data) >> s) & 1 for s in range(7, -1, -1)])
    for b in data:
        bits.extend([(b >> s) & 1 for s in range(7, -1, -1)])
    # 终结符：最多 4 个 0，截齐到字节边界
    term = min(4, data_len * 8 - len(bits))
    bits.extend([0] * term)
    while len(bits) % 8 != 0:
        bits.append(0)
    # 填充码字 0xEC / 0x11 交替
    pads = [0xEC, 0x11]
    idx = 0
    while len(bits) < data_len * 8:
        byte = pads[idx % 2]
        idx += 1
        bits.extend([(byte >> s) & 1 for s in range(7, -1, -1)])
    # 剩余位（在码字之后）
    bits.extend([0] * remainder_bits)
    return bits


def _interleave(data_bits: list, version: int) -> list:
    """按块拆分数据码字、计算纠错码字并交织，返回最终位流。"""
    data_cw = []
    for i in range(0, len(data_bits) - _REMAINDER_BITS[version], 8):
        v = 0
        for b in data_bits[i:i + 8]:
            v = (v << 1) | b
        data_cw.append(v)
    blocks = _BLOCKS[version]
    per_block = len(data_cw) // blocks
    data_blocks = [data_cw[k * per_block:(k + 1) * per_block] for k in range(blocks)]
    ecc_per_block = _ECC_PER_BLOCK[version]
    ecc_blocks = [_rs_ecc(bytes(db), ecc_per_block) for db in data_blocks]
    out = []
    for i in range(per_block):
        for db in data_blocks:
            out.append(db[i])
    for i in range(ecc_per_block):
        for eb in ecc_blocks:
            out.append(eb[i])
    bits = []
    for cw in out:
        bits.extend([(cw >> s) & 1 for s in range(7, -1, -1)])
    return bits


# 每块的纠错码字数（纠错级别 L）。
_ECC_PER_BLOCK = {1: 7, 2: 10, 3: 15, 4: 20, 5: 26, 6: 18}


def _pick_version(data_len: int) -> int:
    for v in range(1, 7):
        # 序列：模式 4bit + 计数 8bit + 数据
        if 12 + data_len * 8 <= _DATA_CODEWORDS[v] * 8:
            return v
    raise ValueError("二维码内容过长（v6 上限 136 字节）")


# ---------------------------------------------------------------------------
# 矩阵绘制
# ---------------------------------------------------------------------------
class _Matrix:
    def __init__(self, version: int):
        self.version = version
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set_function(self, row: int, col: int, dark: bool) -> None:
        if 0 <= row < self.size and 0 <= col < self.size:
            self.modules[row][col] = dark
            self.function[row][col] = True

    def draw_finder(self, row: int, col: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = row + dr, col + dc
                if not (0 <= r < self.size and 0 <= c < self.size):
                    continue
                inside = 0 <= dr <= 6 and 0 <= dc <= 6
                dark = inside and (dr in (0, 6) or dc in (0, 6)
                                   or (2 <= dr <= 4 and 2 <= dc <= 4))
                self.set_function(r, c, dark)

    def draw_function_patterns(self) -> None:
        n = self.size
        self.draw_finder(0, 0)
        self.draw_finder(0, n - 7)
        self.draw_finder(n - 7, 0)
        # 时序图形
        for i in range(8, n - 8):
            dark = i % 2 == 0
            self.set_function(6, i, dark)
            self.set_function(i, 6, dark)
        # 校正图形（跳过与定位图形重叠的三个角）
        centers = _ALIGNMENT_CENTERS[self.version]
        for r in centers:
            for c in centers:
                if self.function[r][c]:
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        dark = max(abs(dr), abs(dc)) != 1
                        self.set_function(r + dr, c + dc, dark)
        # 固定暗模块
        self.set_function(n - 8, 8, True)

    def draw_format_info(self, mask_id: int) -> None:
        n = self.size
        data = (_ECC_LEVEL_L_BITS << 3) | mask_id
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * _FORMAT_GEN)
        bits = ((data << 10) | rem) ^ _FORMAT_MASK

        def bit(i: int) -> bool:
            return ((bits >> i) & 1) == 1

        # 第一份（围绕左上定位图形：col=8 竖条 + row=8 横条）
        for i in range(6):
            self.set_function(i, 8, bit(i))   # (row=i, col=8)
        self.set_function(7, 8, bit(6))
        self.set_function(8, 8, bit(7))
        self.set_function(8, 7, bit(8))
        for i in range(9, 15):
            self.set_function(8, 14 - i, bit(i))  # (row=8, col=5..0)
        # 第二份（右上横条 + 左下竖条）
        for i in range(8):
            self.set_function(8, n - 1 - i, bit(i))   # (row=8, col 右往左)
        for i in range(8, 15):
            self.set_function(n - 15 + i, 8, bit(i))  # (row 自上而下, col=8)
        # 固定暗模块
        self.set_function(n - 8, 8, True)

    def draw_codewords(self, codeword_bits: list) -> None:
        n = self.size
        idx = 0
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5  # 跳过时序列
            upward = (right + 1) & 2 == 0
            for vert in range(n):
                row = n - 1 - vert if upward else vert
                for col in (right, right - 1):
                    if self.function[row][col]:
                        continue
                    dark = idx < len(codeword_bits) and codeword_bits[idx]
                    idx += 1
                    self.modules[row][col] = dark
            right -= 2
        self._data_bits_used = idx

    def mask_bit(self, mask_id: int, row: int, col: int) -> bool:
        if mask_id == 0:
            return (row + col) % 2 == 0
        if mask_id == 1:
            return row % 2 == 0
        if mask_id == 2:
            return col % 3 == 0
        if mask_id == 3:
            return (row + col) % 3 == 0
        if mask_id == 4:
            return (row // 2 + col // 3) % 2 == 0
        if mask_id == 5:
            return (row * col) % 2 + (row * col) % 3 == 0
        if mask_id == 6:
            return ((row * col) % 2 + (row * col) % 3) % 2 == 0
        return ((row + col) % 2 + (row * col) % 3) % 2 == 0

    def apply_mask(self, mask_id: int) -> None:
        for r in range(self.size):
            for c in range(self.size):
                if not self.function[r][c]:
                    self.modules[r][c] ^= self.mask_bit(mask_id, r, c)

    def penalty(self) -> int:
        """ISO 18004 的四条罚分（Nayuki 算法），用于挑选掩码。"""
        n = self.size
        result = 0
        # N1（连续同色）+ N3（1:1:3:1:1 比例图形），按行/列各扫一遍
        for vertical in (False, True):
            for i in range(n):
                run_color = False
                run_len = 0
                run_history = [0] * 7
                for j in range(n):
                    cur = self.modules[j][i] if vertical else self.modules[i][j]
                    if cur == run_color:
                        run_len += 1
                        if run_len == 5:
                            result += 3
                        elif run_len > 5:
                            result += 1
                    else:
                        _qr_add_run(run_len, run_history, n)
                        if not run_color:
                            result += _qr_count_patterns(run_history) * 40
                        run_color = cur
                        run_len = 1
                result += _qr_terminate_and_count(run_color, run_len, run_history, n) * 40

        # N2：2×2 同色块
        for r in range(n - 1):
            for c in range(n - 1):
                if self.modules[r][c] == self.modules[r][c + 1] \
                        == self.modules[r + 1][c] == self.modules[r + 1][c + 1]:
                    result += 3
        # N4：深浅比例
        dark = sum(1 for r in range(n) for c in range(n) if self.modules[r][c])
        total = n * n
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * 10
        return result


def _qr_count_patterns(run_history: list) -> int:
    """N3：run 历史符合 1:1:3:1:1 计数（模式可正反向出现，返回 0/1/2）。"""
    n = run_history[1]
    core = n > 0 and run_history[2] == n and run_history[3] == n * 3 \
        and run_history[4] == n and run_history[5] == n
    if not core:
        return 0
    return (1 if run_history[0] >= n * 4 and run_history[6] >= n else 0) \
        + (1 if run_history[6] >= n * 4 and run_history[0] >= n else 0)


def _qr_add_run(run_len: int, run_history: list, size: int) -> None:
    """把刚结束的 run 压入历史；序列首段额外补一侧浅色边框。"""
    if run_history[0] == 0:
        run_len += size
    run_history.insert(0, run_len)
    run_history.pop()


def _qr_terminate_and_count(run_color: bool, run_len: int, run_history: list,
                            size: int) -> int:
    """行/列扫描收尾：补边框后统计一次 N3 模式。"""
    if run_color:  # 先终结暗色 run
        _qr_add_run(run_len, run_history, size)
        run_len = 0
    run_len += size  # 补另一侧浅色边框
    _qr_add_run(run_len, run_history, size)
    return _qr_count_patterns(run_history)


def encode(text: str) -> dict:
    """编码文本，返回 {"size": n, "modules": [[0/1,...],...]}。"""
    data = text.encode("utf-8")
    version = _pick_version(len(data))
    bits = _bit_buffer_bytes(data, _DATA_CODEWORDS[version], _REMAINDER_BITS[version])
    codeword_bits = _interleave(bits, version)

    best = None
    for mask_id in range(8):
        m = _Matrix(version)
        m.draw_function_patterns()
        m.draw_format_info(0)      # 先用占位 mask 预留 format 格子，避免被码字占用
        m.draw_codewords(codeword_bits)
        m.apply_mask(mask_id)
        m.draw_format_info(mask_id)
        p = m.penalty()
        if best is None or p < best[0]:
            best = (p, m)
    _, m = best
    return {"size": m.size,
            "modules": [[1 if cell else 0 for cell in row] for row in m.modules]}
