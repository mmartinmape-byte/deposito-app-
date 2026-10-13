// OCR local: la foto nunca se envía al servicio que distribuye Tesseract.
(() => {
  const CDN = 'https://cdn.jsdelivr.net/npm/tesseract.js@6.0.1/dist/';
  let library;
  let workerPromise;

  function loadLibrary() {
    if (window.Tesseract) return Promise.resolve(window.Tesseract);
    if (!library) {
      library = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        const fail = () => {
          clearTimeout(timer);
          script.remove();
          library = null;
          reject(new Error('No se pudo cargar el lector. Revisá la conexión o escribí el SKU.'));
        };
        const timer = setTimeout(fail, 20000);
        script.src = CDN + 'tesseract.min.js';
        script.onload = () => {
          clearTimeout(timer);
          if (window.Tesseract) resolve(window.Tesseract);
          else fail();
        };
        script.onerror = fail;
        document.head.appendChild(script);
      });
    }
    return library;
  }

  let progress = () => {};
  async function getWorker() {
    const Tesseract = await loadLibrary();
    if (!workerPromise) {
      workerPromise = Tesseract.createWorker('eng', 1, {
        workerPath: CDN + 'worker.min.js',
        corePath: 'https://cdn.jsdelivr.net/npm/tesseract.js-core@6.0.0',
        logger: m => {
          if (m.status === 'recognizing text') progress('Leyendo SKU… ' + Math.round(m.progress * 100) + '%');
          else progress('Preparando lector…');
        },
      }).then(async worker => {
        await worker.setParameters({
          tessedit_pageseg_mode: Tesseract.PSM.SPARSE_TEXT,
          tessedit_char_whitelist: 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789- ',
          user_defined_dpi: '300',
        });
        return worker;
      });
    }
    return workerPromise;
  }

  window.SkuOCR = {
    async readImage(image, onProgress = () => {}) {
      progress = onProgress;
      let timer;
      try {
        const task = (async () => {
          const worker = await getWorker();
          const { data } = await worker.recognize(image);
          return data.text;
        })();
        return await Promise.race([task, new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('La lectura tardó demasiado. Probá una foto más cercana o escribí el SKU.')), 90000);
        })]);
      } catch (error) {
        const failedWorker = workerPromise;
        workerPromise = null;
        if (failedWorker) failedWorker.then(w => w.terminate()).catch(() => {});
        throw error;
      } finally {
        clearTimeout(timer);
        progress = () => {};
      }
    },
  };
})();
