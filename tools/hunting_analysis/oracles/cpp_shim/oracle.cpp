// Standalone C++ oracle for hunting_analysis.
//
// Extracts the EXACT arithmetic used by the server's damage pipeline, so the
// Python model can be property-tested against it. Links against nothing but
// the C++ standard library; no game headers.
//
// Provides three RPCs over stdin/stdout (one JSON-ish command per line):
//   max_weapon <level> <skill> <attack> <factor>
//   max_melee  <skill> <attack>
//   block      <damage> <armor> <defense> <has_shield> <check_def> <check_arm> <seed>
//
// Build with:  g++ -O2 -std=c++14 oracle.cpp -o oracle
//
// All three functions are copied verbatim from the non-classic branches of
// sources/weapons.cpp (getMaxMeleeDamage, getMaxWeaponDamage) and
// sources/creature.cpp (blockHit's armor+defense math).

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <random>
#include <string>
#include <sstream>

static int32_t getMaxMeleeDamage(int32_t attackSkill, int32_t attackValue) {
    // sources/weapons.cpp:147-150
    return (int32_t)std::ceil((attackSkill * (attackValue * 0.05)) + (attackValue * 0.5));
}

static int32_t getMaxWeaponDamage(int32_t level, int32_t attackSkill,
                                   int32_t attackValue, float attackFactor) {
    // sources/weapons.cpp:152-157 (non-classic branch)
    return (int32_t)std::ceil(
        (2.0 * (attackValue * (attackSkill + 5.8) / 25.0 + (level - 1) / 10.0)) / attackFactor
    );
}

struct BlockOut { int32_t damage; int32_t blocked_defense; int32_t blocked_armor; };

// Use a seeded Mersenne Twister so Python can reproduce the same stream.
// The server uses rand24b() (a custom PRNG). For oracle validation the
// important invariant is that the ARITHMETIC is identical; the RNG stream
// itself is not compared. Python tests therefore pass the roll results
// directly (via stubbed rolls) when they want determinism.
static int32_t uniform_range(std::mt19937& g, int32_t lo, int32_t hi) {
    if (hi < lo) std::swap(lo, hi);
    std::uniform_int_distribution<int32_t> d(lo, hi);
    return d(g);
}

static BlockOut blockHit(int32_t damage, int32_t armor, int32_t defense,
                         bool hasShield, bool checkDefense, bool checkArmor,
                         uint32_t seed) {
    // sources/creature.cpp:961-1017 (armor+defense arithmetic).
    BlockOut r{damage, 0, 0};
    std::mt19937 g(seed);
    if (checkDefense && hasShield && defense > 0) {
        int32_t minDef = defense / 2;
        int32_t maxDef = defense;
        r.damage -= uniform_range(g, minDef, maxDef);
        if (r.damage <= 0) { r.damage = 0; r.blocked_defense = 1; return r; }
    }
    if (checkArmor) {
        int32_t armorValue = armor, minA = 0, maxA = 0;
        if (armorValue > 1) {
            minA = (int32_t)std::ceil(armorValue * 0.475);
            maxA = (int32_t)std::ceil((armorValue * 0.475) - 1 + minA);
        } else if (armorValue == 1) {
            minA = 1; maxA = 1;
        }
        if (maxA > 0) {
            r.damage -= uniform_range(g, minA, maxA);
            if (r.damage <= 0) { r.damage = 0; r.blocked_armor = 1; return r; }
        }
    }
    return r;
}

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream iss(line);
        std::string cmd; iss >> cmd;
        if (cmd == "max_weapon") {
            int32_t level, skill, attack; float factor;
            iss >> level >> skill >> attack >> factor;
            std::cout << getMaxWeaponDamage(level, skill, attack, factor) << "\n" << std::flush;
        } else if (cmd == "max_melee") {
            int32_t skill, attack; iss >> skill >> attack;
            std::cout << getMaxMeleeDamage(skill, attack) << "\n" << std::flush;
        } else if (cmd == "block") {
            int32_t damage, armor, defense, hasShield, checkDef, checkArm;
            uint32_t seed;
            iss >> damage >> armor >> defense >> hasShield >> checkDef >> checkArm >> seed;
            BlockOut o = blockHit(damage, armor, defense, hasShield, checkDef, checkArm, seed);
            std::cout << o.damage << " " << o.blocked_defense << " " << o.blocked_armor
                      << "\n" << std::flush;
        } else if (cmd == "ping") {
            std::cout << "pong\n" << std::flush;
        } else if (cmd == "quit" || cmd == "exit") {
            break;
        } else {
            std::cout << "ERR unknown\n" << std::flush;
        }
    }
    return 0;
}
