// Copyright (c) 2018 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <common/system.h>
#include <qt/bitcoin.h>
#include <qt/guiutil.h>
#include <qt/test/optiontests.h>
#include <test/util/setup_common.h>

#include <QSettings>
#include <QTest>

#include <univalue.h>

//! Entry point for BitcoinApplication tests.
void OptionTests::optionTests() {
    // Test regression https://github.com/bitcoin/bitcoin/issues/24457. Ensure
    // that setting integer prune value doesn't cause an exception to be thrown
    // in the OptionsModel constructor
    gArgs.LockSettings([&](util::Settings &settings) {
        settings.forced_settings.erase("prune");
        settings.rw_settings["prune"] = 3814;
    });
    gArgs.WriteSettingsFile();
    OptionsModel{};
    gArgs.LockSettings(
        [&](util::Settings &settings) { settings.rw_settings.erase("prune"); });
    gArgs.WriteSettingsFile();
}

void OptionTests::extractFilter() {
    QString filter = QString("Partially Signed Transaction (Binary) (*.psbt)");
    QCOMPARE(GUIUtil::ExtractFirstSuffixFromFilter(filter), "psbt");

    filter = QString("Image (*.png *.jpg)");
    QCOMPARE(GUIUtil::ExtractFirstSuffixFromFilter(filter), "png");
}
