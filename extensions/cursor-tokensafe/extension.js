const vscode = require('vscode');
const { exec } = require('child_process');
const path = require('path');
const fs = require('fs');

function activate(context) {
    const statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = "$(zap) TokenSafe";
    statusBarItem.tooltip = "TokenSafe: Active (Zero-Loss Token Economy)";
    statusBarItem.command = "tokensafe.showStats";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    const projectRoot = path.dirname(path.dirname(__dirname));
    const candidateTokensafe = path.join(projectRoot, "bin", "tokensafe");
    const cliPath = fs.existsSync(candidateTokensafe) ? candidateTokensafe : path.join(projectRoot, "bin", "agy-tokens");
    const dashboardHtml = path.join(projectRoot, "data", "dashboard.html");

    const disposableStats = vscode.commands.registerCommand('tokensafe.showStats', () => {
        if (fs.existsSync(cliPath)) {
            exec(`python3 "${cliPath}" stats`, (error, stdout) => {
                if (stdout) {
                    vscode.window.showInformationMessage(`TokenSafe Savings:\n${stdout.split('\n').slice(0, 5).join('\n')}`);
                } else {
                    vscode.window.showInformationMessage("TokenSafe is actively optimizing context tokens.");
                }
            });
        } else {
            vscode.window.showInformationMessage("TokenSafe is active and protecting context tokens.");
        }
    });

    const disposableDash = vscode.commands.registerCommand('tokensafe.openDashboard', () => {
        if (fs.existsSync(dashboardHtml)) {
            vscode.env.openExternal(vscode.Uri.file(dashboardHtml));
        } else {
            vscode.window.showInformationMessage("Generating TokenSafe dashboard...");
            if (fs.existsSync(cliPath)) {
                exec(`python3 "${cliPath}" html`, () => {
                    if (fs.existsSync(dashboardHtml)) {
                        vscode.env.openExternal(vscode.Uri.file(dashboardHtml));
                    }
                });
            }
        }
    });

    context.subscriptions.push(disposableStats, disposableDash);
}

function deactivate() {}

module.exports = {
    activate,
    deactivate
};
