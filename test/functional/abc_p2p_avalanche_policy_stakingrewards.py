# Copyright (c) 2023 The Bitcoin developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test the resolution of staking rewards via avalanche."""
import random
import time

from test_framework.address import P2SH_OP_TRUE, SCRIPT_UNSPENDABLE
from test_framework.avatools import (
    assert_response,
    can_find_inv_in_poll,
    get_ava_p2p_interface,
)
from test_framework.blocktools import create_block, create_coinbase
from test_framework.key import ECPubKey
from test_framework.messages import (
    XEC,
    AvalancheProofVoteResponse,
    AvalancheVote,
    AvalancheVoteError,
    CBlockHeader,
    CTxOut,
    ToHex,
)
from test_framework.test_framework import BitcoinTestFramework
from test_framework.txtools import pad_tx
from test_framework.util import assert_equal, uint256_hex

STAKING_REWARDS_COINBASE_RATIO_PERCENT = 10
QUORUM_NODE_COUNT = 16


class ABCStakingRewardsPolicyTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 1
        self.noban_tx_relay = True
        self.extra_args = [
            [
                "-avaproofstakeutxodustthreshold=1000000",
                "-avaproofstakeutxoconfirmations=1",
                "-avacooldown=0",
                "-avaminquorumstake=0",
                "-avaminavaproofsnodecount=0",
                "-avalanchestakingrewards=1",
            ],
        ]

    def run_test(self):
        node = self.nodes[0]

        now = int(time.time())
        node.setmocktime(now)

        # Build a fake quorum of nodes. The payout script is SCRIPT_UNSPENDABLE
        # for all the proofs.
        def get_quorum():
            return [
                get_ava_p2p_interface(self, node) for _ in range(0, QUORUM_NODE_COUNT)
            ]

        # Pick one node from the quorum for polling.
        quorum = get_quorum()

        def wait_for_finalized_proof(proofid):
            def finalize_proof(proofid):
                can_find_inv_in_poll(
                    quorum, proofid, response=AvalancheProofVoteResponse.ACTIVE
                )
                return node.getrawavalancheproof(uint256_hex(proofid)).get(
                    "finalized", False
                )

            self.wait_until(lambda: finalize_proof(proofid))

        for peer in quorum:
            wait_for_finalized_proof(peer.proof.proofid)

        poll_node = quorum[0]

        assert node.getavalancheinfo()["ready_to_poll"] is True

        now += 60 * 60 + 1
        node.setmocktime(now)

        self.generate(node, 1)

        # Get block reward
        coinbase = node.getblock(node.getbestblockhash(), 2)["tx"][0]
        block_reward = sum([vout["value"] for vout in coinbase["vout"]])
        staking_rewards_amount = int(
            block_reward * XEC * STAKING_REWARDS_COINBASE_RATIO_PERCENT / 100
        )

        avakey = ECPubKey()
        avakey.set(bytes.fromhex(node.getavalanchekey()))

        def has_accepted_tip(tip_expected):
            hash_tip_final = int(tip_expected, 16)
            can_find_inv_in_poll(quorum, hash_tip_final)
            return node.getbestblockhash() == tip_expected

        def has_finalized_tip(tip_expected):
            hash_tip_final = int(tip_expected, 16)
            can_find_inv_in_poll(quorum, hash_tip_final)
            return node.isfinalblock(tip_expected)

        def create_cb(payout_script, amount, height):
            # Build a coinbase with no staking reward
            cb = create_coinbase(height)
            # Keep only the block reward output
            cb.vout = cb.vout[:1]
            # Change the block reward to account for the staking reward
            cb.vout[0].nValue = int(block_reward * XEC - amount)
            # Add the staking reward output
            if payout_script and amount > 0:
                cb.vout.append(
                    CTxOut(
                        nValue=amount,
                        scriptPubKey=payout_script,
                    )
                )

            pad_tx(cb)
            cb.calc_sha256()
            return cb

        def new_block(tip, payout_script, amount, expect_accepted=None):
            # Create a new block paying to the specified payout script
            cb = create_cb(payout_script, amount, node.getblockcount() + 1)
            block = create_block(
                int(tip, 16), cb, node.getblock(tip)["time"] + 1, version=4
            )
            block.solve()
            node.submitblock(ToHex(block))

            # Check the current tip is what we expect
            matches_policy = (
                (
                    payout_script == SCRIPT_UNSPENDABLE
                    and amount >= staking_rewards_amount
                )
                if expect_accepted is None
                else expect_accepted
            )

            expected_tip = block.hash if matches_policy else tip
            assert_equal(node.getbestblockhash(), expected_tip)

            # Poll and check the node votes what we expect
            poll_node.send_poll([block.sha256])
            expected_vote = (
                AvalancheVoteError.ACCEPTED
                if matches_policy
                else AvalancheVoteError.PARKED
            )
            assert_response(
                poll_node, avakey, [AvalancheVote(expected_vote, block.sha256)]
            )

            # Vote yes on this block until the node accepts it
            self.wait_until(lambda: has_accepted_tip(block.hash))
            assert_equal(node.getbestblockhash(), block.hash)

            poll_node.send_poll([block.sha256])
            assert_response(
                poll_node,
                avakey,
                [AvalancheVote(AvalancheVoteError.ACCEPTED, block.sha256)],
            )

            return block

        tip = node.getbestblockhash()
        new_block(
            tip,
            SCRIPT_UNSPENDABLE,
            staking_rewards_amount - 1,
            expect_accepted=False,
        )

        # Base cases that we always want to test
        cases = [
            (SCRIPT_UNSPENDABLE, staking_rewards_amount),
            # Another script but all else equal
            (P2SH_OP_TRUE, staking_rewards_amount),
            # Pay no staking reward at all
            (None, 0),
        ]

        # Add some more random cases
        for _ in range(0, 10):
            script = SCRIPT_UNSPENDABLE if random.randrange(0, 2) else P2SH_OP_TRUE
            amount = random.randrange(0, staking_rewards_amount * 2)
            cases.append((script, amount))

        # Shuffle the test cases so we get varied test coverage
        random.shuffle(cases)
        for script, amount in cases:
            self.log.info(
                f"Staking rewards test case: script: {script.hex() if script is not None else 'None'}, amount: {amount}"
            )
            new_block(node.getbestblockhash(), script, amount)

        # Check a rejection case
        tip = node.getbestblockhash()
        self.log.info("Staking rewards rejection test case")

        reject = ""
        with node.assert_debug_log(expected_msgs=["policy-bad-staking-reward"]):
            reject = new_block(tip, P2SH_OP_TRUE, staking_rewards_amount).hash

        reject_hash = int(reject, 16)
        with node.wait_for_debug_log(
            [f"Avalanche invalidated block {reject}".encode()],
            chatty_callable=lambda: can_find_inv_in_poll(
                quorum, reject_hash, AvalancheVoteError.PARKED
            ),
        ):
            pass

        # Build a block on the accepted tip and the chain continues as normal
        tip = new_block(tip, SCRIPT_UNSPENDABLE, staking_rewards_amount).hash
        assert_equal(node.getbestblockhash(), tip)

        # Tip should finalize
        self.wait_until(lambda: has_finalized_tip(tip))

        self.log.info("Staking rewards multiple blocks test case")

        def new_unsubmitted_block(tip, payout_script, amount, height):
            # Create a new block paying to the specified payout script
            cb = create_cb(payout_script, amount, height)
            block = create_block(
                int(tip, 16), cb, node.getblockheader(tip)["time"] + 1, version=4
            )
            block.solve()

            # Only submit the header
            node.submitheader(CBlockHeader(block).serialize().hex())
            return block

        height = node.getblockcount()
        out_of_order_block = new_unsubmitted_block(
            tip, SCRIPT_UNSPENDABLE, staking_rewards_amount, height + 1
        )
        newtip = new_unsubmitted_block(
            out_of_order_block.hash,
            SCRIPT_UNSPENDABLE,
            staking_rewards_amount,
            height + 2,
        )

        # Submit the blocks out of order so that FindMostWorkChain selects newtip as most work
        node.submitblock(ToHex(newtip))
        node.submitblock(ToHex(out_of_order_block))
        tip = newtip.hash

        # Both blocks should still have staking reward winners computed
        assert node.hasstakingreward(out_of_order_block.hash)
        assert node.hasstakingreward(tip)

        # New tip should finalize
        assert_equal(node.getbestblockhash(), tip)
        self.wait_until(lambda: has_finalized_tip(tip))


if __name__ == "__main__":
    ABCStakingRewardsPolicyTest().main()
