const mineflayer = require('mineflayer');
const [,, host, portStr, username] = process.argv;
const port = parseInt(portStr, 10) || 25565;
const TIMEOUT_MS = 8000;

const bot = mineflayer.createBot({
    host, port, username,
    auth: 'offline',
    version: false,
    hideErrors: true,
});

let done = false;
function finish(result) {
    if (done) return;
    done = true;
    console.log(JSON.stringify(result));
    try { bot.quit(); } catch(_) {}
    setTimeout(() => process.exit(0), 300);
}

const timer = setTimeout(() => {
    finish({ joined: false, reason: 'timeout' });
}, TIMEOUT_MS);

bot.once('spawn', () => {
    clearTimeout(timer);
    setTimeout(() => {
        try {
            bot.chat('This server is not protected. Learn more by adding @proxyshlart on discord.');
        } catch(_) {}
        setTimeout(() => finish({ joined: true, reason: null }), 1500);
    }, 1500);
});

bot.once('kicked', (reason) => {
    clearTimeout(timer);
    let reasonStr = '';
    try {
        const parsed = typeof reason === 'string' ? JSON.parse(reason) : reason;
        reasonStr = JSON.stringify(parsed).toLowerCase();
    } catch(_) {
        reasonStr = String(reason).toLowerCase();
    }
    const whitelisted =
        reasonStr.includes('whitelist') ||
        reasonStr.includes('not whitelisted') ||
        reasonStr.includes('white-list') ||
        reasonStr.includes('not on the whitelist');
    finish({ joined: false, reason: whitelisted ? 'whitelist' : reasonStr.slice(0, 120) });
});

bot.once('error', (err) => {
    clearTimeout(timer);
    finish({ joined: false, reason: 'error: ' + err.message });
});