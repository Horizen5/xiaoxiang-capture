import { CliOptions } from "./cli";

type Logger = {
    info: (...messages: unknown[]) => void;
    error: (...messages: unknown[]) => void;
    main_debug: (...messages: unknown[]) => void;
    frida_debug: (...messages: unknown[]) => void;
};

// 带上时间戳：以前日志只有顺序没有时刻，「连上就断」这类问题没法判断
// 是几秒内连了 20 次，还是隔了 20 分钟才断一次。
const stamp = () => new Date().toTimeString().slice(0, 8);

const create_logger = (options: CliOptions): Logger => ({
    info: (...messages) => {
        console.log(`[${stamp()}]`, ...messages);
    },
    error: (...messages) => {
        console.error(`[${stamp()}]`, ...messages);
    },
    main_debug: (...messages) => {
        if (options.debugMain) {
            console.log(`[${stamp()}]`, ...messages);
        }
    },
    frida_debug: (...messages) => {
        if (options.debugFrida) {
            console.log(`[${stamp()}]`, ...messages);
        }
    },
});

export { Logger, create_logger };
