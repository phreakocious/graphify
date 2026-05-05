// Closure-as-module factory pattern. The factory declares closures
// internally and returns them via a shorthand object literal — a
// canonical TS idiom we need the AST extractor to surface.
export function buildEngine(config: { dim: number }) {
    async function probeForward(x: number[]): Promise<number[]> {
        return x.map((v) => v * config.dim);
    }

    function injectForward(slot: number, v: number[]): void {
        probeForward(v);
    }

    const embeddingGenerate = (token: number) => {
        return [token, token];
    };

    return {
        probeForward,
        injectForward,
        embeddingGenerate,
    };
}
