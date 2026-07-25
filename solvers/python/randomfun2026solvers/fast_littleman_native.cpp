// Hot loop for fast_littleman.py.
//
// This is deliberately a narrow C ABI rather than a Python extension module:
// the Python implementation owns parsing, validation, and the public API.  It
// serializes an already-parsed immutable program plus one case into compact
// whitespace-separated integers.  The native loop returns the final validation
// state the same way.  No Node, WASM, files, or third-party C++ dependencies.

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

using i64 = std::int64_t;

struct Pos {
  int x{}, y{};
  bool operator==(const Pos& o) const { return x == o.x && y == o.y; }
};
struct PosHash {
  std::size_t operator()(const Pos& p) const {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(p.x)) << 32) ^
           static_cast<std::uint32_t>(p.y);
  }
};
struct Dir { int x{}, y{}; };
static Pos add(Pos p, Dir d) { return {p.x + d.x, p.y + d.y}; }
static Dir cw(Dir d) { return {-d.y, d.x}; }
static Dir ccw(Dir d) { return {d.y, -d.x}; }

struct Room {
  int x1{}, y1{}, x2{}, y2{}, kind{}, sx{-1}, sy{-1};
  bool contains(Pos p) const { return x1 < p.x && p.x < x2 && y1 < p.y && p.y < y2; }
  bool border(Pos p) const {
    return x1 <= p.x && p.x <= x2 && y1 <= p.y && p.y <= y2 &&
           (p.x == x1 || p.x == x2 || p.y == y1 || p.y == y2);
  }
};
struct Pipe {
  int length{}, src{}, dst{};
  Dir dst_side{};
};
struct Runner {
  int id{}, room{};
  Pos pos{};
  Dir dir{1, 0};
  i64 a{}, b{}, bp{};
  bool halted{}, blocked{};
};
struct LiteralKey {
  Pos p{};
  Dir d{};
  bool operator==(const LiteralKey& o) const {
    return p == o.p && d.x == o.d.x && d.y == o.d.y;
  }
};
struct LiteralHash {
  std::size_t operator()(const LiteralKey& k) const {
    return PosHash{}(k.p) ^ (static_cast<unsigned>(k.d.x + 1) << 5) ^
           static_cast<unsigned>(k.d.y + 1);
  }
};
struct Program {
  int width{}, height{}, input_room{-1}, output_room{-1};
  bool can_split{};
  std::vector<unsigned char> grid;
  std::vector<Room> rooms;
  std::vector<Pipe> pipes;
  std::unordered_map<Pos, std::vector<int>, PosHash> bindings;
  std::unordered_map<LiteralKey, i64, LiteralHash> literals;
  char at(Pos p) const {
    if (p.x < 0 || p.y < 0 || p.x >= width || p.y >= height) return ' ';
    return static_cast<char>(grid[static_cast<std::size_t>(p.y) * width + p.x]);
  }
};

struct Case {
  std::vector<std::vector<i64>> inputs;
  bool has_expected{};
  std::vector<std::vector<i64>> expected;
  bool has_frames{};
  std::vector<std::vector<std::vector<unsigned char>>> frame_rounds;
  std::uint64_t max_ticks{};
};

template <typename T>
bool readv(std::istringstream& in, T& v) {
  return static_cast<bool>(in >> v);
}

bool parse_request(const char* raw, Program& p, Case& c, std::string& error) {
  std::istringstream in(raw ? raw : "");
  std::string magic;
  if (!(in >> magic) || magic != "FLM1") { error = "bad native request magic"; return false; }
  if (!readv(in, p.width) || !readv(in, p.height) || p.width < 0 || p.height < 0) {
    error = "bad grid dimensions"; return false;
  }
  p.grid.resize(static_cast<std::size_t>(p.width) * p.height);
  for (auto& ch : p.grid) {
    int value;
    if (!readv(in, value)) { error = "truncated grid"; return false; }
    ch = static_cast<unsigned char>(value);
    p.can_split |= ch == 'Y';
  }
  int nrooms;
  if (!readv(in, nrooms) || nrooms < 0) { error = "bad room count"; return false; }
  p.rooms.resize(nrooms);
  for (auto& r : p.rooms) {
    if (!(in >> r.x1 >> r.y1 >> r.x2 >> r.y2 >> r.kind >> r.sx >> r.sy)) {
      error = "truncated rooms"; return false;
    }
  }
  int npipes;
  if (!readv(in, npipes) || npipes < 0) { error = "bad pipe count"; return false; }
  p.pipes.resize(npipes);
  for (auto& pipe : p.pipes) {
    if (!(in >> pipe.length >> pipe.src >> pipe.dst >> pipe.dst_side.x >> pipe.dst_side.y)) {
      error = "truncated pipes"; return false;
    }
  }
  int nbindings;
  if (!readv(in, nbindings) || nbindings < 0) { error = "bad binding count"; return false; }
  for (int i = 0; i < nbindings; ++i) {
    Pos pos;
    int count;
    if (!(in >> pos.x >> pos.y >> count) || count < 0) {
      error = "bad binding"; return false;
    }
    auto& ids = p.bindings[pos];
    ids.resize(count);
    for (int& id : ids) if (!readv(in, id)) { error = "truncated binding"; return false; }
  }
  int nliterals;
  if (!readv(in, nliterals) || nliterals < 0) { error = "bad literal count"; return false; }
  for (int i = 0; i < nliterals; ++i) {
    LiteralKey key;
    i64 value;
    if (!(in >> key.p.x >> key.p.y >> key.d.x >> key.d.y >> value)) {
      error = "truncated literals"; return false;
    }
    p.literals.emplace(key, value);
  }
  if (!(in >> p.input_room >> p.output_room)) { error = "missing io rooms"; return false; }
  int rounds;
  if (!readv(in, rounds) || rounds < 0) { error = "bad input rounds"; return false; }
  c.inputs.resize(rounds);
  for (auto& round : c.inputs) {
    int count;
    if (!readv(in, count) || count < 0) { error = "bad input count"; return false; }
    round.resize(count);
    for (i64& v : round) if (!readv(in, v)) { error = "truncated input"; return false; }
  }
  int expected_flag;
  if (!readv(in, expected_flag)) { error = "missing expected flag"; return false; }
  c.has_expected = expected_flag != 0;
  if (c.has_expected) {
    if (!readv(in, rounds) || rounds < 0) { error = "bad expected rounds"; return false; }
    c.expected.resize(rounds);
    for (auto& round : c.expected) {
      int count;
      if (!readv(in, count) || count < 0) { error = "bad expected count"; return false; }
      round.resize(count);
      for (i64& v : round) if (!readv(in, v)) { error = "truncated expected"; return false; }
    }
  }
  int frame_flag;
  if (!readv(in, frame_flag)) { error = "missing frame flag"; return false; }
  c.has_frames = frame_flag != 0;
  if (c.has_frames) {
    if (!readv(in, rounds) || rounds < 0) { error = "bad frame rounds"; return false; }
    c.frame_rounds.resize(rounds);
    for (auto& round : c.frame_rounds) {
      int frame_count;
      if (!readv(in, frame_count) || frame_count < 0) { error = "bad frame count"; return false; }
      round.resize(frame_count);
      for (auto& frame : round) {
        int pixels;
        if (!readv(in, pixels) || pixels < 0) { error = "bad pixel count"; return false; }
        frame.resize(pixels);
        for (auto& pixel : frame) {
          int value;
          if (!readv(in, value) || value < 0 || value > 15) {
            error = "bad frame pixel"; return false;
          }
          pixel = static_cast<unsigned char>(value);
        }
      }
    }
  }
  if (!readv(in, c.max_ticks)) { error = "missing tick cap"; return false; }
  return true;
}

struct Result {
  std::vector<i64> output;
  std::uint64_t step{};
  bool halted{}, passed_known{}, passed{};
  std::string reason, fatal;
  Pos fatal_pos{-1, -1};
};

struct Display {
  int room{}, width{}, height{}, cursor{};
  std::vector<unsigned char> current, next;
};

class Machine {
 public:
  Machine(const Program& program, const Case& test) : p(program), c(test) {
    values.reserve(p.pipes.size());
    for (const auto& pipe : p.pipes) values.emplace_back(pipe.length);
    std::vector<std::size_t> spawn_rooms;
    for (std::size_t rid = 0; rid < p.rooms.size(); ++rid) {
      const auto& room = p.rooms[rid];
      if (room.kind == 0 && room.sx >= 0) spawn_rooms.push_back(rid);
      if (room.kind == 3) {
        int width = room.x2 - room.x1 - 1, height = room.y2 - room.y1 - 1;
        displays.push_back(
            {static_cast<int>(rid), width, height, 0,
             std::vector<unsigned char>(width * height),
             std::vector<unsigned char>(width * height)});
      }
    }
    // Initial creation order follows the @ cells in row-major order, not room
    // discovery order. A later Y split retains the parent's position here.
    std::sort(spawn_rooms.begin(), spawn_rooms.end(), [&](std::size_t a, std::size_t b) {
      const auto& lhs = p.rooms[a];
      const auto& rhs = p.rooms[b];
      return std::tie(lhs.sy, lhs.sx) < std::tie(rhs.sy, rhs.sx);
    });
    for (std::size_t rid : spawn_rooms) {
      const auto& room = p.rooms[rid];
      runners.push_back(
          {static_cast<int>(runners.size()), static_cast<int>(rid), {room.sx, room.sy}});
    }
    next_id = static_cast<int>(runners.size());
    if (!c.has_expected && !c.has_frames) {
      for (const auto& round : c.inputs) for (i64 v : round) input.push_back(v);
    } else if (!c.inputs.empty()) {
      for (i64 v : c.inputs[0]) input.push_back(v);
    }
    if (c.has_frames) {
      for (const auto& round : c.frame_rounds) {
        for (const auto& frame : round) expected_frames.push_back(frame);
        cumulative.push_back(expected_frames.size());
      }
    } else {
      for (const auto& round : c.expected) {
        expected.insert(expected.end(), round.begin(), round.end());
        cumulative.push_back(expected.size());
      }
    }
    release_satisfied();
  }

  Result run() {
    while (step < c.max_ticks) {
      if (!fatal.empty()) return finish(fatal, true, false);
      if (c.has_frames && matched_frames >= expected_frames.size())
        return finish("output-settled", true, output.empty());
      if (!c.has_frames && c.has_expected && output.size() >= expected.size())
        return finish("output-settled", true, true);
      bool active = false;
      for (const auto& r : runners) active |= !r.halted;
      if (!active && !output_in_flight()) {
        bool known = c.has_expected;
        bool pass = !known || output == expected;
        return finish("done", known, pass);
      }
      tick();
    }
    return finish("tick-cap", c.has_expected, false);
  }

 private:
  const Program& p;
  const Case& c;
  std::vector<std::vector<i64>> values;  // INT64_MIN is empty; real values may be MIN, so separate occupancy.
  std::vector<std::vector<unsigned char>> occupied;
  std::vector<Runner> runners;
  std::vector<Display> displays;
  int next_id{};
  std::deque<i64> input;
  std::vector<i64> output, expected;
  std::vector<std::vector<unsigned char>> expected_frames;
  std::size_t matched_frames{};
  std::vector<std::size_t> cumulative;
  std::size_t released{};
  std::uint64_t step{};
  std::string fatal;
  Pos fatal_pos{-1, -1};

  // Lazily create occupancy after values sizes are known.
  void ensure_occupancy() {
    if (!occupied.empty()) return;
    occupied.reserve(values.size());
    for (const auto& pipe : values) occupied.emplace_back(pipe.size(), 0);
  }
  Result finish(std::string reason, bool known, bool pass) {
    bool halted = true;
    for (const auto& r : runners) halted &= r.halted;
    return {output, step, halted, known, pass, std::move(reason), fatal, fatal_pos};
  }
  bool output_in_flight() {
    ensure_occupancy();
    if (p.output_room < 0) return false;
    for (std::size_t pid = 0; pid < p.pipes.size(); ++pid)
      if (p.pipes[pid].dst == p.output_room)
        for (auto bit : occupied[pid]) if (bit) return true;
    return false;
  }
  void die(const char* why, Pos pos) { fatal = why; fatal_pos = pos; }
  void tick() {
    ensure_occupancy();
    ++step;
    for (std::size_t pid = 0; pid < values.size(); ++pid) {
      auto& vals = values[pid];
      auto& occ = occupied[pid];
      for (std::size_t i = vals.size(); i-- > 1;) {
        if (!occ[i] && occ[i - 1]) {
          vals[i] = vals[i - 1]; occ[i] = 1; occ[i - 1] = 0;
        }
      }
    }
    io();
    if (!fatal.empty()) return;
    execute();
    if (!fatal.empty()) return;
    execute_displays();
    if (!fatal.empty()) return;
    move();
  }
  int input_pipe() const {
    if (p.input_room < 0) return -1;
    for (std::size_t i = 0; i < p.pipes.size(); ++i)
      if (p.pipes[i].src == p.input_room) return static_cast<int>(i);
    return -1;
  }
  int output_pipe() const {
    if (p.output_room < 0) return -1;
    for (std::size_t i = 0; i < p.pipes.size(); ++i)
      if (p.pipes[i].dst == p.output_room) return static_cast<int>(i);
    return -1;
  }
  void io() {
    int out = output_pipe();
    if (out >= 0 && occupied[out].back()) {
      i64 v = values[out].back(); occupied[out].back() = 0; output.push_back(v);
      if (c.has_expected) {
        std::size_t i = output.size() - 1;
        if (i >= expected.size() || v != expected[i]) { die("wrong-output", {-1, -1}); return; }
        release_satisfied();
      }
    }
    int in = input_pipe();
    if (in >= 0 && !input.empty() && !occupied[in][0]) {
      values[in][0] = input.front(); input.pop_front(); occupied[in][0] = 1;
    }
  }
  void release_satisfied() {
    std::size_t progress = c.has_frames ? matched_frames : output.size();
    while (released < cumulative.size() && progress >= cumulative[released]) {
      ++released;
      if (released < c.inputs.size())
        for (i64 item : c.inputs[released]) input.push_back(item);
    }
  }
  int display_pipe(int room, int side) const {
    // 0 top/ADDR, 1 left/DATA, 2 bottom/SWAP.
    const auto& r = p.rooms[room];
    for (std::size_t i = 0; i < p.pipes.size(); ++i) {
      const auto& pipe = p.pipes[i];
      if (pipe.dst != room) continue;
      Dir d = pipe.dst_side;
      if ((side == 0 && d.x == 0 && d.y == 1) ||
          (side == 1 && d.x == 1 && d.y == 0) ||
          (side == 2 && d.x == 0 && d.y == -1))
        return static_cast<int>(i);
    }
    return -1;
  }
  bool take_display(int pid, i64& value) {
    if (pid < 0 || !occupied[pid].back()) return false;
    value = values[pid].back();
    occupied[pid].back() = 0;
    return true;
  }
  void execute_displays() {
    for (auto& display : displays) {
      i64 value;
      if (take_display(display_pipe(display.room, 0), value)) {
        if (value < 0 || value >= display.width * display.height) {
          die("display-address", {-1, -1}); return;
        }
        display.cursor = static_cast<int>(value);
      }
      if (take_display(display_pipe(display.room, 1), value)) {
        if (value < 0 || value > 15) { die("display-color", {-1, -1}); return; }
        display.next[display.cursor] = static_cast<unsigned char>(value);
        display.cursor = (display.cursor + 1) % static_cast<int>(display.next.size());
      }
      if (take_display(display_pipe(display.room, 2), value)) {
        if (value != 0 && value != 1) { die("display-swap", {-1, -1}); return; }
        display.current = display.next;
        if (value == 0) {
          std::fill(display.next.begin(), display.next.end(), 0);
          display.cursor = 0;
        }
        if (c.has_frames) {
          if (matched_frames >= expected_frames.size() ||
              display.current != expected_frames[matched_frames]) {
            die("wrong-frame", {-1, -1}); return;
          }
          ++matched_frames;
          release_satisfied();
        }
      }
    }
  }
  static i64 wrap_add(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) + static_cast<std::uint64_t>(b));
  }
  static i64 wrap_sub(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) - static_cast<std::uint64_t>(b));
  }
  static i64 wrap_mul(i64 a, i64 b) {
    return static_cast<i64>(static_cast<std::uint64_t>(a) * static_cast<std::uint64_t>(b));
  }
  const std::vector<int>* binding(Pos pos) const {
    auto it = p.bindings.find(pos);
    return it == p.bindings.end() ? nullptr : &it->second;
  }
  void pipe_op(Runner& r, char op) {
    const auto* ids = binding(r.pos);
    if (!ids || ids->empty() || (*ids)[0] < 0) { die("no-pipe", r.pos); return; }
    if (op == 'q') {
      int pid = (*ids)[0]; i64 count = 0;
      for (auto bit : occupied[pid]) count += bit != 0;
      r.bp = count; return;
    }
    if (op == 's') {
      int pid = (*ids)[0];
      if (occupied[pid][0]) r.blocked = true;
      else { values[pid][0] = r.a; occupied[pid][0] = 1; }
      return;
    }
    if (op == 'S') {
      for (int pid : *ids) if (occupied[pid][0]) { r.blocked = true; return; }
      for (int pid : *ids) { values[pid][0] = r.a; occupied[pid][0] = 1; }
      return;
    }
    int pid = -1;
    if (op == 'r') pid = (*ids)[0];
    else for (int candidate : *ids) if (occupied[candidate].back()) { pid = candidate; break; }
    if (pid < 0 || !occupied[pid].back()) { r.blocked = true; return; }
    r.a = values[pid].back(); occupied[pid].back() = 0;
    if (op == 'U') r.dir = p.pipes[pid].dst_side;
  }
  void execute() {
    std::vector<Runner> spawned;
    spawned.reserve(runners.size());
    std::unordered_map<Pos, Runner*, PosHash> occupied;
    int live = 0;
    for (auto& r : runners) if (!r.halted) { occupied[r.pos] = &r; ++live; }
    auto birth = [&](Runner& child) {
      const auto& room = p.rooms[child.room];
      if (room.border(child.pos) || !room.contains(child.pos)) {
        die("wall", child.pos); return;
      }
      auto it = occupied.find(child.pos);
      if (it != occupied.end() && !it->second->halted) {
        it->second->halted = true;
        child.halted = true;
        occupied.erase(it);
        live -= 2;
      } else {
        occupied[child.pos] = &child;
      }
    };
    for (auto& r : runners) {
      r.blocked = false;
      if (r.halted) continue;
      char op = p.at(r.pos);
      if (op == '@') op = ' ';
      if (op >= '0' && op <= '9') r.a = op - '0';
      else switch (op) {
        case ' ': case '.': break;
        case '`': {
          auto it = p.literals.find({r.pos, r.dir});
          if (it != p.literals.end()) r.a = it->second;
          break;
        }
        case 'M': r.b = r.a; break;
        case 'W': std::swap(r.a, r.b); break;
        case 'N': r.a = wrap_sub(0, r.a); break;
        case '+': r.a = wrap_add(r.a, r.b); break;
        case '-': r.a = wrap_sub(r.a, r.b); break;
        case '*': r.a = wrap_mul(r.a, r.b); break;
        case '%':
          if (r.b == 0) r.a = 0;
          else {
            i64 rem = r.a % r.b;
            if (rem && ((rem < 0) != (r.b < 0))) rem = wrap_add(rem, r.b);
            r.a = rem;
          }
          break;
        case '/': {
          i64 dividend = r.a, divisor = r.b;
          if (divisor == 0) { r.a = 0; r.b = dividend; }
          else if (dividend == INT64_MIN && divisor == -1) { r.a = INT64_MIN; r.b = 0; }
          else {
            i64 q = dividend / divisor, rem = dividend % divisor;
            if (rem && ((rem < 0) != (divisor < 0))) { --q; rem += divisor; }
            r.a = q; r.b = rem;
          }
          break;
        }
        case '&': r.a &= r.b; break;
        case '|': r.a |= r.b; break;
        case '~': r.a ^= r.b; break;
        case '{': r.a = (r.b < 0 || r.b > 63) ? 0 : static_cast<i64>(static_cast<std::uint64_t>(r.a) << r.b); break;
        case '}': r.a = r.b < 0 ? 0 : (r.b > 63 ? (r.a < 0 ? -1 : 0) : (r.a >> r.b)); break;
        case '>': r.dir = {1, 0}; break;
        case '<': r.dir = {-1, 0}; break;
        case '^': r.dir = {0, -1}; break;
        case 'v': case 'V': r.dir = {0, 1}; break;
        case 'X': if (r.a > 0) r.dir = cw(r.dir); else if (r.a < 0) r.dir = ccw(r.dir); break;
        case 'b': r.bp = r.a; break;
        case 'm': r.bp = wrap_sub(r.bp, 1); break;
        case 'd': if (r.bp > 0) r.dir = cw(r.dir); break;
        case 'a': if (r.bp > 0) r.dir = ccw(r.dir); break;
        case ']': r.bp >>= 1; break;
        case 'x': r.dir = (r.bp & 1) ? cw(r.dir) : ccw(r.dir); break;
        case 'Y': {
          Dir old = r.dir; Pos origin = r.pos;
          occupied.erase(origin);
          r.dir = cw(old); r.pos = add(origin, r.dir); r.blocked = true;
          Dir nd = ccw(old);
          spawned.push_back(
              {next_id++, r.room, add(origin, nd), nd, r.a, r.b, r.bp, false, true});
          ++live;
          birth(spawned.back()); if (!fatal.empty()) return;
          birth(r); if (!fatal.empty()) return;
          if (live > 65536) { die("too-many-runners", origin); return; }
          break;
        }
        case 'H': r.halted = true; occupied.erase(r.pos); --live; break;
        case 's': case 'S': case 'r': case 'R': case 'U': case 'q':
          pipe_op(r, op); if (!fatal.empty()) return; break;
        default: die("bad-op", r.pos); return;
      }
    }
    runners.insert(runners.end(), spawned.begin(), spawned.end());
  }
  void move() {
    // Every checked-in solver currently has one runner per compute room and no
    // Y.  Rooms are disjoint, so those runners can never touch one another.
    // Avoid constructing three hash tables on every tick in this overwhelmingly
    // common validation path.
    if (!p.can_split) {
      for (auto& r : runners) {
        if (r.halted || r.blocked) continue;
        r.pos = add(r.pos, r.dir);
        const auto& room = p.rooms[r.room];
        if (room.border(r.pos) || !room.contains(r.pos)) { die("wall", r.pos); return; }
      }
      return;
    }
    std::unordered_map<Pos, std::vector<int>, PosHash> arrivals;
    std::unordered_map<Pos, int, PosHash> current;
    std::vector<Pos> dest(runners.size());
    std::vector<unsigned char> moving(runners.size());
    for (std::size_t i = 0; i < runners.size(); ++i)
      if (!runners[i].halted) current[runners[i].pos] = static_cast<int>(i);
    for (std::size_t i = 0; i < runners.size(); ++i) {
      auto& r = runners[i];
      if (!r.halted && !r.blocked) {
        moving[i] = 1;
        dest[i] = add(r.pos, r.dir);
        arrivals[dest[i]].push_back(static_cast<int>(i));
      }
    }
    std::vector<unsigned char> touching(runners.size());
    for (const auto& [pos, ids] : arrivals) {
      if (ids.size() > 1) for (int id : ids) touching[id] = 1;
      auto it = current.find(pos);
      if (it != current.end()) {
        int occupant = it->second;
        if (!moving[occupant]) {
          touching[occupant] = 1;
          for (int id : ids) touching[id] = 1;
        } else {
          for (int id : ids) {
            if (dest[occupant] == runners[id].pos) {
              touching[occupant] = 1;
              touching[id] = 1;
            }
          }
        }
      }
    }
    for (std::size_t i = 0; i < runners.size(); ++i)
      if (touching[i]) runners[i].halted = true;
    for (std::size_t i = 0; i < runners.size(); ++i) {
      auto& r = runners[i];
      if (!moving[i]) continue;
      r.pos = dest[i];
      if (r.halted) continue;
      const auto& room = p.rooms[r.room];
      if (room.border(r.pos) || !room.contains(r.pos)) { die("wall", r.pos); return; }
    }
  }
};

std::string encode(const Result& r) {
  std::ostringstream out;
  out << "FLMR1 " << r.step << ' ' << r.halted << ' ' << r.passed_known << ' '
      << r.passed << ' ' << r.reason << ' ' << (r.fatal.empty() ? "-" : r.fatal)
      << ' ' << r.fatal_pos.x << ' ' << r.fatal_pos.y << ' ' << r.output.size();
  for (i64 value : r.output) out << ' ' << value;
  return out.str();
}

}  // namespace

extern "C" const char* flm_run(const char* request) {
  Program program;
  Case test;
  std::string error;
  std::string result;
  if (!parse_request(request, program, test, error)) {
    result = "FLME1 " + error;
  } else {
    result = encode(Machine(program, test).run());
  }
  char* copy = static_cast<char*>(std::malloc(result.size() + 1));
  if (!copy) return nullptr;
  std::memcpy(copy, result.c_str(), result.size() + 1);
  return copy;
}

extern "C" void flm_free(const char* value) {
  std::free(const_cast<char*>(value));
}
