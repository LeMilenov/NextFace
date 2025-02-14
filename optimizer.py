from image import Image, ImageFolder, overlayImage, saveImage
from torchmetrics.functional.image import image_gradients
from gaussiansmoothing import GaussianSmoothing, smoothImage
from projection import estimateCameraPosition
from textureloss import TextureLoss
from pipeline import Pipeline
from config import Config
from utils import *
import argparse
import pickle
import tqdm
import sys
class Optimizer:

    def __init__(self, outputDir, config):
        self.config = config
        self.device = config.device
        self.verbose = config.verbose
        self.framesNumber = 0
        self.pipeline = Pipeline(self.config)

        if self.config.lamdmarksDetectorType == 'fan':
            from landmarksfan import LandmarksDetectorFAN
            self.landmarksDetector = LandmarksDetectorFAN(self.pipeline.morphableModel.landmarksMask, self.device)
        elif self.config.lamdmarksDetectorType == 'mediapipe':
            from landmarksmediapipe import LandmarksDetectorMediapipe
            self.landmarksDetector = LandmarksDetectorMediapipe(self.pipeline.morphableModel.landmarksMask, self.device)
        else:
            raise ValueError(f'lamdmarksDetectorType must be one of [mediapipe, fan] but was {self.config.lamdmarksDetectorType}')

        self.textureLoss = TextureLoss(self.device)

        self.inputImage = None
        self.landmarks = None
        torch.set_grad_enabled(False)
        self.smoothing = GaussianSmoothing(3, 3, 1.0, 2).to(self.device)
        self.outputDir = outputDir + '/'
        self.debugDir = self.outputDir + '/debug/'
        mkdir_p(self.outputDir)
        mkdir_p(self.debugDir)
        mkdir_p(self.outputDir + '/checkpoints/')
        mkdir_p(self.debugDir + '/results/')
        mkdir_p(self.debugDir + '/mesh/')
        
        self.vEnhancedDiffuse = None
        self.vEnhancedSpecular = None
        self.vEnhancedRoughness = None

    def saveParameters(self, outputFileName):

        dict = {
            'vShapeCoeff': self.pipeline.vShapeCoeff.detach().cpu().numpy(),
            'vAlbedoCoeff': self.pipeline.vAlbedoCoeff.detach().cpu().numpy(),
            'vExpCoeff': self.pipeline.vExpCoeff.detach().cpu().numpy(),
            'vRotation': self.pipeline.vRotation.detach().cpu().numpy(),
            'vTranslation': self.pipeline.vTranslation.detach().cpu().numpy(),
            'vFocals': self.pipeline.vFocals.detach().cpu().numpy(),
            'vShCoeffs': self.pipeline.vShCoeffs.detach().cpu().numpy(),
            'screenWidth':self.pipeline.renderer.screenWidth,
            'screenHeight': self.pipeline.renderer.screenHeight,
            'sharedIdentity': self.pipeline.sharedIdentity

        }
        if self.vEnhancedDiffuse is not None:
            dict['vEnhancedDiffuse'] = self.vEnhancedDiffuse.detach().cpu().numpy()
        if self.vEnhancedSpecular is not None:
            dict['vEnhancedSpecular'] = self.vEnhancedSpecular.detach().cpu().numpy()
        if self.vEnhancedRoughness is not None:
            dict['vEnhancedRoughness'] = self.vEnhancedRoughness.detach().cpu().numpy()

        handle = open(outputFileName, 'wb')
        pickle.dump(dict, handle, pickle.HIGHEST_PROTOCOL)
        handle.close()

    def loadParameters(self, pickelFileName):
        handle = open(pickelFileName, 'rb')
        assert handle is not None
        dict = pickle.load(handle)
        self.pipeline.vShapeCoeff = torch.tensor(dict['vShapeCoeff']).to(self.device)
        self.pipeline.vAlbedoCoeff = torch.tensor(dict['vAlbedoCoeff']).to(self.device)
        self.pipeline.vExpCoeff = torch.tensor(dict['vExpCoeff']).to(self.device)
        self.pipeline.vRotation = torch.tensor(dict['vRotation']).to(self.device)
        self.pipeline.vTranslation = torch.tensor(dict['vTranslation']).to(self.device)
        self.pipeline.vFocals = torch.tensor(dict['vFocals']).to(self.device)
        self.pipeline.vShCoeffs = torch.tensor(dict['vShCoeffs']).to(self.device)
        self.pipeline.renderer.screenWidth = int(dict['screenWidth'])
        self.pipeline.renderer.screenHeight = int(dict['screenHeight'])
        self.pipeline.sharedIdentity = bool(dict['sharedIdentity'])

        if "vEnhancedDiffuse" in dict:
            self.vEnhancedDiffuse = torch.tensor(dict['vEnhancedDiffuse']).to(self.device)

        if "vEnhancedSpecular" in dict:
            self.vEnhancedSpecular = torch.tensor(dict['vEnhancedSpecular']).to(self.device)

        if "vEnhancedRoughness" in dict:
            self.vEnhancedRoughness = torch.tensor(dict['vEnhancedRoughness']).to(self.device)

        handle.close()
        self.enableGrad()

    def loadAlbedoParameters(self, pickelFileName):
        """
        for my specific use case i will load the checkpoint for stage 1 and load only the albedoCoeff from step 2 of my synthesized test
        """
        handle = open(pickelFileName, 'rb')
        assert handle is not None
        dict = pickle.load(handle)
        # self.pipeline.vShapeCoeff = torch.tensor(dict['vShapeCoeff']).to(self.device)
        self.pipeline.vAlbedoCoeff = torch.tensor(dict['vAlbedoCoeff']).to(self.device)
        # self.pipeline.vExpCoeff = torch.tensor(dict['vExpCoeff']).to(self.device)
        # self.pipeline.vRotation = torch.tensor(dict['vRotation']).to(self.device)
        # self.pipeline.vTranslation = torch.tensor(dict['vTranslation']).to(self.device)
        # self.pipeline.vFocals = torch.tensor(dict['vFocals']).to(self.device)
        # self.pipeline.vShCoeffs = torch.tensor(dict['vShCoeffs']).to(self.device)
        # self.pipeline.renderer.screenWidth = int(dict['screenWidth'])
        # self.pipeline.renderer.screenHeight = int(dict['screenHeight'])
        # self.pipeline.sharedIdentity = bool(dict['sharedIdentity'])

        # if "vEnhancedDiffuse" in dict:
        #     self.vEnhancedDiffuse = torch.tensor(dict['vEnhancedDiffuse']).to(self.device)

        # if "vEnhancedSpecular" in dict:
        #     self.vEnhancedSpecular = torch.tensor(dict['vEnhancedSpecular']).to(self.device)

        # if "vEnhancedRoughness" in dict:
        #     self.vEnhancedRoughness = torch.tensor(dict['vEnhancedRoughness']).to(self.device)

        handle.close()
        self.enableGrad()

    def enableGrad(self):
        self.pipeline.vShapeCoeff.requires_grad = True
        self.pipeline.vAlbedoCoeff.requires_grad = True
        self.pipeline.vExpCoeff.requires_grad = True
        self.pipeline.vRotation.requires_grad = True
        self.pipeline.vTranslation.requires_grad = True
        self.pipeline.vFocals.requires_grad = True
        self.pipeline.vShCoeffs.requires_grad = True

    def setImage(self, imagePath, sharedIdentity = False):
        '''
        set image to estimate face reflectance and geometry
        :param imagePath: drive path to the image
        :param sharedIdentity: if true than the shape and albedo coeffs are equal to 1, as they belong to the same person identity
        :return:
        '''
        if os.path.isfile(imagePath):
            self.inputImage = Image(imagePath, self.device, self.config.maxResolution)
        else:
            self.inputImage = ImageFolder(imagePath, self.device, self.config.maxResolution)

        self.framesNumber = self.inputImage.tensor.shape[0]
        #self.inputImage = Image(imagePath, self.device)
        self.pipeline.renderer.screenWidth = self.inputImage.width
        self.pipeline.renderer.screenHeight = self.inputImage.height

        print('detecting landmarks using:', self.config.lamdmarksDetectorType)
        landmarks = self.landmarksDetector.detect(self.inputImage.tensor)
        #assert (landmarks.shape[0] == 1)  # can only handle single subject in image
        assert (landmarks.dim() == 3 and landmarks.shape[2] == 2)
        self.landmarks = landmarks
        for i in range(self.framesNumber):
            imagesLandmark = self.landmarksDetector.drawLandmarks(self.inputImage.tensor[i], self.landmarks[i])
            cv2.imwrite(self.outputDir  + '/landmarks' + str(i) + '.png', cv2.cvtColor(imagesLandmark, cv2.COLOR_BGR2RGB) )
        self.pipeline.initSceneParameters(self.framesNumber, sharedIdentity)
        self.initCameraPos() #always init the head pose (rotation + translation)
        self.enableGrad()

    def initCameraPos(self):
        print('init camera pose...', file=sys.stderr, flush=True)
        association = self.pipeline.morphableModel.landmarksAssociation
        vertices = self.pipeline.computeShape()
        headPoints = vertices[:, association]
        rot, trans = estimateCameraPosition(self.pipeline.vFocals, self.inputImage.center,
                                    self.landmarks, headPoints, self.pipeline.vRotation,
                                    self.pipeline.vTranslation)

        self.pipeline.vRotation = rot.clone().detach()
        self.pipeline.vTranslation = trans.clone().detach()
        
    def getTextureIndex(self, i):
        if self.pipeline.sharedIdentity:
            return 0
        return i
    
    def debugFrame(self, image, target, diff, diffuseTexture, specularTexture, roughnessTexture, outputPrefix):
        for i in range(image.shape[0]):
            diffuse = cv2.resize(cv2.cvtColor(diffuseTexture[self.getTextureIndex(i)].detach().cpu().numpy(), cv2.COLOR_BGR2RGB), (target.shape[2], target.shape[1]))
            spec = cv2.resize(cv2.cvtColor(specularTexture[self.getTextureIndex(i)].detach().cpu().numpy(), cv2.COLOR_BGR2RGB),  (target.shape[2], target.shape[1]))
            rough = roughnessTexture[self.getTextureIndex(i)].detach().cpu().numpy()
            rough = cv2.cvtColor(cv2.resize(rough, (target.shape[2], target.shape[1])), cv2.COLOR_GRAY2RGB)

            res = cv2.hconcat([cv2.cvtColor(image[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB),
                                cv2.cvtColor(target[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB),
                                cv2.cvtColor(diff[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB)])
            tex = cv2.hconcat([diffuse, spec, rough])
            
            # Concatenate vertically - combined image with textures 
            debugFrame = cv2.vconcat([np.power(np.clip(res, 0.0, 1.0), 1.0 / 2.2) * 255, tex * 255])
            
            cv2.imwrite(outputPrefix  + '.png', debugFrame)
            
    def debugRender(self, image, outputPrefix):
        for i in range(image.shape[0]):
            res = cv2.hconcat([cv2.cvtColor(image[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB)])
            
            # Concatenate vertically - combined image with textures 
            debugFrame = cv2.vconcat([np.power(np.clip(res, 0.0, 1.0), 1.0 / 2.2) * 255])
            
            cv2.imwrite(outputPrefix  + '.png', debugFrame)

    def debugFrameGrad(self, image, target, grad_shapeCoeff, grad_expCoeff, grad_shCoeff, grad_rotation, grad_translation, grad_albedo, outputPrefix):
        
        grad_images = [grad_shapeCoeff, grad_expCoeff, grad_shCoeff, grad_rotation, grad_translation, grad_albedo]
        for i in range(image.shape[0]):
            diff = (image[i] - target[i]).abs()

            # Prepare the gradients
            grad_imgs = []
            for grad in grad_images:
                grad_img = grad[i].detach().cpu().numpy()
                # Normalize the grad image
                min_val, max_val = np.min(grad_img), np.max(grad_img)
                grad_img = (grad_img - min_val) / (max_val - min_val)
                # rest if the steps
                grad_img_resized = cv2.resize(grad_img, (target.shape[2], target.shape[1]))
                grad_img_rgb = cv2.cvtColor(grad_img_resized, cv2.COLOR_GRAY2RGB)
                grad_imgs.append(grad_img_rgb)

            # Concatenate horizontally
            top_row = cv2.hconcat([cv2.cvtColor(image[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB),
                               cv2.cvtColor(target[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB),
                               cv2.cvtColor(diff.detach().cpu().numpy(), cv2.COLOR_BGR2RGB)])
            middle_row = cv2.hconcat(grad_imgs[:3])
            bottom_row = cv2.hconcat(grad_imgs[3:])

            # Concatenate vertically
            debugFrame = cv2.vconcat([np.power(np.clip(top_row, 0.0, 1.0), 1.0 / 2.2) * 255, middle_row * 255, bottom_row * 255])
            
            cv2.imwrite(outputPrefix  + '_frame' + str(i) + '.png', debugFrame)       
    def debugImageGrad(self, image, target, grad_img_1,grad_img_2, outputPrefix):
        gamma_correction_factor = 1.0 / 2.2
        for i in range(image.shape[0]):
            grad_img_2[i, :, :, 0:2] = grad_img_2[i, :, :, 2:3]  # Copy the blue channel to the red and green channels
            # Apply gamma correction to the first three images
            corrected_images = [image[i], target[i], grad_img_1[i]]
            corrected_images = [np.power(np.clip(img.detach().cpu().numpy(), 0.0, 1.0), gamma_correction_factor) for img in corrected_images]
            corrected_images.append(cv2.cvtColor(grad_img_2[i].detach().cpu().numpy(), cv2.COLOR_BGR2RGB))  # Add the fourth image without gamma correction
            # Concatenate horizontally
            res = cv2.hconcat([cv2.cvtColor(img, cv2.COLOR_BGR2RGB) for img in corrected_images])
            # Concatenate vertically - combined image with textures
            debugFrame = cv2.vconcat([res * 255])
            cv2.imwrite(outputPrefix + 'gradient_frame' + str(i) + '.png', debugFrame)
            
    def debugIteration(self, image, target, diff, diffuseOnlyVertexRender, lightingOnlyVertexRender, outputPrefix):
        """render a debug picture to see how an iteration looks like

        Args:
            image ([1, width, height, 3] tensor): final render
            target ([1, width, height, 3] tensor): input picture
            diff ( [1, width, height, 3] tensor): difference between image and target
            diffuseOnlyVertexRender ( [1, width, height, 4] tensor): render but only with the diffuse Albedo 
            lightingOnlyVertexRender ( [1, width, height, 4] tensor): render but only with the SH 
            outputPrefix (string): where we save the files
        """
        # Converting tensors to numpy arrays
        image = image.squeeze(0).detach().cpu().numpy()
        target = target.squeeze(0).detach().cpu().numpy()
        diff = diff.squeeze(0).detach().cpu().numpy()
        diffuseOnlyVertexRender = diffuseOnlyVertexRender[..., :3].squeeze(0).detach().cpu().numpy()
        lightingOnlyVertexRender = lightingOnlyVertexRender[..., :3].squeeze(0).detach().cpu().numpy()


        # # Adjust colors and clamp
        image = np.power(np.clip(image, 0.0, 1.0), 1.0 / 2.2)
        target = np.clip(target, 0.0, 1.0)
        diff = np.clip(diff, 0.0, 1.0)
        diffuseOnlyVertexRender = np.clip(diffuseOnlyVertexRender, 0.0, 1.0)
        lightingOnlyVertexRender = np.clip(lightingOnlyVertexRender, 0.0, 1.0)

        # OpenCV assumes images to be in BGR format. Convert RGB to BGR
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        target = cv2.cvtColor(target, cv2.COLOR_RGB2BGR)
        diff = cv2.cvtColor(diff, cv2.COLOR_RGB2BGR)
        diffuseOnlyVertexRender = cv2.cvtColor(diffuseOnlyVertexRender, cv2.COLOR_RGB2BGR)
        lightingOnlyVertexRender = cv2.cvtColor(lightingOnlyVertexRender, cv2.COLOR_RGB2BGR)

        # Concatenate images horizontally and vertically
        res = cv2.hconcat([image, target, diff])
        ref = cv2.hconcat([diffuseOnlyVertexRender, lightingOnlyVertexRender])
        # add a black padding so that ref matches size of res (res is 3 pictures ref is 2)
        if ref.shape[1] < res.shape[1]:
            pad_width = res.shape[1] - ref.shape[1]
            padding = np.zeros((ref.shape[0], pad_width, ref.shape[2]), dtype=np.float32)
            ref = cv2.hconcat([ref, padding])
            
        debugFrame = cv2.vconcat([res, ref])
        # Multiplying by 255 because OpenCV uses 8-bit values + add gamma        
        debugFrame = (debugFrame* 255).astype(np.uint8)

        # Save the image
        cv2.imwrite(outputPrefix + '.png', debugFrame)
    def debugTensor(self, debugTensor):
        """display a tensor of shape [x, y, 3]

        Args:
            debugTensor (_type_): _description_
        """
        import matplotlib.pyplot as plt

        # Convert your tensor to a numpy array. If your tensor is on the GPU, bring it back to CPU first
        debugTensor = debugTensor.detach().cpu().numpy()

        # Convert the mask to a 2D array if it is not already
        if len(debugTensor.shape) > 2:
            debugTensor = debugTensor.mean(axis=-1)

        # Display the mask
        plt.imshow(debugTensor)
        plt.colorbar(label='debugTensor')
        plt.show()
        
    def regStatModel(self, coeff, var):
        loss = ((coeff * coeff) / var).mean()
        return loss

    def plotLoss(self, lossArr, index, fileName):
        import matplotlib.pyplot as plt
        plt.figure(index)
        plt.plot(lossArr)
        plt.scatter(np.arange(0, len(lossArr)).tolist(), lossArr, c='red')
        plt.savefig(fileName)

    def landmarkLoss(self, cameraVertices, landmarks):
        return self.pipeline.landmarkLoss(cameraVertices, landmarks, self.pipeline.vFocals, self.inputImage.center)

    def runStep1(self):
        print("1/3 => Optimizing head pose and expressions using landmarks...", file=sys.stderr, flush=True)
        torch.set_grad_enabled(True)

        params = [
            {'params': self.pipeline.vRotation, 'lr': 0.02},
            {'params': self.pipeline.vTranslation, 'lr': 0.02},
            {'params': self.pipeline.vExpCoeff, 'lr': 0.02},
            #{'params': self.pipeline.vShapeCoeff, 'lr': 0.02}
        ]

        if self.config.optimizeFocalLength:
            params.append({'params': self.pipeline.vFocals, 'lr': 0.02})

        optimizer = torch.optim.Adam(params)
        losses = []

        for iter in tqdm.tqdm(range(self.config.iterStep1)):
            optimizer.zero_grad()
            vertices = self.pipeline.computeShape()
            cameraVertices = self.pipeline.transformVertices(vertices)
            loss = self.landmarkLoss(cameraVertices, self.landmarks)
            loss += 0.1 * self.regStatModel(self.pipeline.vExpCoeff, self.pipeline.morphableModel.expressionPcaVar)
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
            if self.verbose:
                print(iter, '=>', loss.item())
            if self.config.debugFrequency > 0 and iter % self.config.debugFrequency == 0:
                    # save obj
                    cameraNormals = self.pipeline.morphableModel.computeNormals(cameraVertices) # only used of obj (might be too slow)
                    saveObj(self.debugDir + '/mesh/' + self.renderer+'_step1_iter' + str(iter)+'.obj',
                            'material' + str(iter) + '.mtl',
                            cameraVertices[0],
                            self.pipeline.faces32,
                            cameraNormals[0],
                            self.pipeline.morphableModel.uvMap,
                            self.debugDir + 'diffuseMap_' + str(self.getTextureIndex(0)) + '.png')

        self.plotLoss(losses, 1, self.outputDir + 'checkpoints/stage1_loss.png')
        self.saveParameters(self.outputDir + 'checkpoints/stage1_output.pickle')
                    
    def runStep2(self):
        print("2/3 => Optimizing shape, statistical albedos, expression, head pose and scene light...", file=sys.stderr, flush=True)
        torch.set_grad_enabled(True)
        
        # self.pipeline.renderer.samples = 8
        inputTensor = torch.pow(self.inputImage.tensor, self.inputImage.gamma)
       
        optimizer = torch.optim.Adam([
            # {'params': self.pipeline.vShapeCoeff, 'lr': 0.05},
            # {'params': self.pipeline.vExpCoeff, 'lr': 0.05},
            {'params': self.pipeline.vShCoeffs, 'lr': 0.005},
            {'params': self.pipeline.vAlbedoCoeff, 'lr': 0.007}
        ])
        losses = []
        
        for iter in tqdm.tqdm(range(self.config.iterStep2 + 1)):
            if iter == 100:
                optimizer.add_param_group({'params': self.pipeline.vShapeCoeff, 'lr': 0.01}) #0.01
                optimizer.add_param_group({'params': self.pipeline.vExpCoeff, 'lr': 0.01}) # 0.01
                optimizer.add_param_group({'params': self.pipeline.vRotation, 'lr': 0.0001})
                optimizer.add_param_group({'params': self.pipeline.vTranslation, 'lr': 0.0001})
            optimizer.zero_grad() 
           
            vertices, diffAlbedo, specAlbedo = self.pipeline.morphableModel.computeShapeAlbedo(self.pipeline.vShapeCoeff, self.pipeline.vExpCoeff, self.pipeline.vAlbedoCoeff)
            cameraVerts = self.pipeline.camera.transformVertices(vertices, self.pipeline.vTranslation, self.pipeline.vRotation)
            diffuseTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(diffAlbedo)
            specularTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(specAlbedo)
            roughTextures = self.pipeline.vRoughness.detach().clone() if self.vEnhancedRoughness is None else self.vEnhancedRoughness.detach().clone()
            # clamp values to not have errors
            diffuseTextures = diffuseTextures.clamp(0,1)
            specularTextures = specularTextures.clamp(0,1)
            roughTextures = roughTextures.clamp(0,1)

            # IMAGE IS [X, Y, 4]
            # render -> updates the scene as well as the params
            
            if self.renderer == 'vertex':
                rgba_img = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo) # vertex based
                # mask_alpha = self.getMask(cameraVerts, diffAlbedo) #redondant                
                mask_alpha = rgba_img[...,3:]                
            elif self.renderer == 'redner':
                rgba_img = self.pipeline.render(cameraVerts, diffuseTextures, specularTextures) #redner
                mask_alpha = rgba_img[...,3:]
                
            elif self.renderer == 'mitsuba':
                rgba_img = self.pipeline.renderMitsuba(cameraVerts, diffuseTextures, specularTextures) #mitsuba
                mask_alpha = self.getMask(cameraVerts, diffAlbedo)
                    
            if self.config.smoothing :
                smoothedImage = smoothImage(rgba_img[..., 0:3], self.smoothing)            
                diff = mask_alpha * (smoothedImage - inputTensor).abs()
            else :
                diff = mask_alpha * (rgba_img[..., 0:3] - inputTensor).abs()
            photoLoss = 1000.* diff.mean()
            landmarksLoss = self.config.weightLandmarksLossStep2 *  self.landmarkLoss(cameraVerts, self.landmarks)
            regLoss = 0.0001 * self.pipeline.vShCoeffs.pow(2).mean()
            regLoss += self.config.weightAlbedoReg * self.regStatModel(self.pipeline.vAlbedoCoeff, self.pipeline.morphableModel.diffuseAlbedoPcaVar)
            regLoss += self.config.weightShapeReg * self.regStatModel(self.pipeline.vShapeCoeff, self.pipeline.morphableModel.shapePcaVar)
            regLoss += self.config.weightExpressionReg * self.regStatModel(self.pipeline.vExpCoeff, self.pipeline.morphableModel.expressionPcaVar)

            loss = photoLoss + landmarksLoss + regLoss
            losses.append(loss.item())
            loss.backward()
            # grad_loss = loss.grad
            # grad_shapeCoeff = self.pipeline.vShapeCoeff.grad
            # grad_expCoeff = self.pipeline.vExpCoeff.grad
            # grad_shCoeff = self.pipeline.vShCoeffs.grad
            # grad_rotation = self.pipeline.vRotation.grad
            # grad_translation = self.pipeline.vTranslation.grad
            # grad_albedo = self.pipeline.vAlbedoCoeff.grad
            optimizer.step()
            
            if self.verbose:
                print(iter, '. photo Loss:', loss,
                      '. landmarks Loss: ', landmarksLoss.item(),
                      '. regLoss: ', regLoss.item())
                print(f"Iteration {iter:03d}: Loss {self.renderer} = {losses[0]:6f}", end='\r')

            if self.config.debugFrequency > 0 and iter % self.config.debugFrequency == 0:
                self.debugFrame(rgba_img[..., 0:3], inputTensor, diff, diffuseTextures, specularTextures, roughTextures, self.debugDir + '/results/'+self.renderer+'_step2_' + str(iter))
                # generate one with mitsuba for reference
                # image_grad = image_gradients(rgba_img)
                # self.debugImageGrad(rgba_img[..., 0:3], inputTensor, image_grad[0], image_grad[1], self.debugDir + '/Baseline/mitsuba/mitsuba_gradient_' + str(iter))
                # self.debugFrameGrad(rgba_img[..., 0:3], inputTensor, grad_shapeCoeff, grad_expCoeff, grad_shCoeff, grad_rotation, grad_translation, grad_albedo,self.debugDir + '/results/'+self.renderer+'_gradient' + str(iter) )
                if self.renderer == 'vertex':
                    mitsuba_img = self.pipeline.renderMitsuba(cameraVerts, diffuseTextures, specularTextures) #mitsuba
                    self.debugRender(mitsuba_img[..., 0:3],self.debugDir + '/results/ref')
                    lightingVertexRender = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo, lightingOnly=True)
                    albedoVertexRender = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo, albedoOnly=True)
                    self.debugIteration(rgba_img[..., 0:3], inputTensor,diff, albedoVertexRender, lightingVertexRender, self.debugDir + '/results/'+self.renderer+ str(iter)+'_detailled_step2') # custom made
                # also save obj
                cameraNormals = self.pipeline.morphableModel.computeNormals(cameraVerts) # only used of obj (might be too slow)
                for i in range(inputTensor.shape[0]):
                    saveObj(self.debugDir + '/mesh/' + self.renderer+'_step2_iter' + str(iter)+'.obj',
                            'material' + str(iter) + '.mtl',
                            cameraVerts[i],
                            self.pipeline.faces32,
                            cameraNormals[i],
                            self.pipeline.morphableModel.uvMap,
                            self.debugDir + 'diffuseMap_' + str(self.getTextureIndex(i)) + '.png')
                   
        self.plotLoss(losses, 1, self.outputDir + 'checkpoints/stage2_loss.png')
        self.saveParameters(self.outputDir + 'checkpoints/stage2_output.pickle')

    def runStep3(self):
        print("3/3 => finetuning albedos, shape, expression, head pose and scene light...", file=sys.stderr, flush=True)
        torch.set_grad_enabled(True)
        # self.pipeline.renderer.samples = 8

        inputTensor = torch.pow(self.inputImage.tensor, self.inputImage.gamma)

        vertices, diffAlbedo, specAlbedo = self.pipeline.morphableModel.computeShapeAlbedo(self.pipeline.vShapeCoeff, self.pipeline.vExpCoeff, self.pipeline.vAlbedoCoeff)
        vDiffTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(diffAlbedo).detach().clone() if self.vEnhancedDiffuse is None else self.vEnhancedDiffuse.detach().clone()
        vSpecTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(specAlbedo).detach().clone() if self.vEnhancedSpecular is None else self.vEnhancedSpecular.detach().clone()
        vRoughTextures = self.pipeline.vRoughness.detach().clone() if self.vEnhancedRoughness is None else self.vEnhancedRoughness.detach().clone()

        refDiffTextures = vDiffTextures.detach().clone()
        refSpecTextures = vSpecTextures.detach().clone()
        refRoughTextures = vRoughTextures.detach().clone()
        vDiffTextures.requires_grad = True
        vSpecTextures.requires_grad = True
        vRoughTextures.requires_grad = True
         # clamp values to not have errors
         
        # vDiffTextures = torch.clamp(vDiffTextures,0,1)
        # vSpecTextures = torch.clamp(vSpecTextures,0,1)
        # vRoughTextures = torch.clamp(vRoughTextures,0,1)
        
        optimizer = torch.optim.Adam([
            {'params': vDiffTextures, 'lr': 0.005},
            {'params': vSpecTextures, 'lr': 0.02},
            {'params': vRoughTextures, 'lr': 0.02}
        ])
        ''''
        {'params': self.pipeline.vShCoeffs, 'lr': 0.005 * 2.},
        {'params': self.pipeline.vShapeCoeff, 'lr': 0.01},
        {'params': self.pipeline.vExpCoeff, 'lr': 0.01},
        {'params': self.pipeline.vRotation, 'lr': 0.0005},
        {'params': self.pipeline.vTranslation, 'lr': 0.0005}'''

        losses = []

        for iter in tqdm.tqdm(range(self.config.iterStep3 + 1)):
            optimizer.zero_grad()
            # torch.cuda.empty_cache() # clear out the cache
            vertices, diffAlbedo, specAlbedo = self.pipeline.morphableModel.computeShapeAlbedo(self.pipeline.vShapeCoeff, self.pipeline.vExpCoeff, self.pipeline.vAlbedoCoeff)
            cameraVerts = self.pipeline.camera.transformVertices(vertices, self.pipeline.vTranslation, self.pipeline.vRotation)

            if self.renderer == 'vertex':
                rgba_img = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo) # vertex based
                # mask_alpha = self.getMask(cameraVerts, diffAlbedo) #redondant                
                mask_alpha = rgba_img[...,3:]                
            elif self.renderer == 'redner':
                rgba_img = self.pipeline.render(cameraVerts, vDiffTextures, vSpecTextures) #redner
                mask_alpha = rgba_img[...,3:]
                
            elif self.renderer == 'mitsuba':
                rgba_img = self.pipeline.renderMitsuba(cameraVerts, vDiffTextures, vSpecTextures, vRoughTextures) #mitsuba
                mask_alpha = self.getMask(cameraVerts, diffAlbedo)
                
            if self.config.smoothing :
                smoothedImage = smoothImage(rgba_img[..., 0:3], self.smoothing)            
                diff = mask_alpha * (smoothedImage - inputTensor).abs()
            else :
                diff = mask_alpha * (rgba_img[..., 0:3] - inputTensor).abs()

            #loss =  diff.mean(dim=-1).sum() / float(self.framesNumber)
            loss = 1000.0 * diff.mean()
            loss += 0.2 * (self.textureLoss.regTextures(vDiffTextures, refDiffTextures, ws = self.config.weightDiffuseSymmetryReg, wr =  self.config.weightDiffuseConsistencyReg, wc = self.config.weightDiffuseConsistencyReg, wsm = self.config.weightDiffuseSmoothnessReg, wm = 0.) + \
                    self.textureLoss.regTextures(vSpecTextures, refSpecTextures, ws = self.config.weightSpecularSymmetryReg, wr = self.config.weightSpecularConsistencyReg, wc = self.config.weightSpecularConsistencyReg, wsm = self.config.weightSpecularSmoothnessReg, wm = 0.5) + \
                    self.textureLoss.regTextures(vRoughTextures, refRoughTextures, ws = self.config.weightRoughnessSymmetryReg, wr = self.config.weightRoughnessConsistencyReg, wc = self.config.weightRoughnessConsistencyReg, wsm = self.config.weightRoughnessSmoothnessReg, wm = 0.))
            loss += 0.0001 * self.pipeline.vShCoeffs.pow(2).mean()
            loss += self.config.weightExpressionReg * self.regStatModel(self.pipeline.vExpCoeff, self.pipeline.morphableModel.expressionPcaVar)
            loss += self.config.weightShapeReg * self.regStatModel(self.pipeline.vShapeCoeff, self.pipeline.morphableModel.shapePcaVar)
            loss += self.config.weightLandmarksLossStep3 * self.landmarkLoss(cameraVerts, self.landmarks)

            losses.append(loss.item())

            loss.backward()
            optimizer.step()
            if self.verbose:
                print(iter, ' => Loss:', loss.item())

            if self.config.debugFrequency > 0 and iter % self.config.debugFrequency == 0:
                self.debugFrame(rgba_img[..., 0:3], inputTensor, diff, vDiffTextures, vSpecTextures, vRoughTextures, self.debugDir + '/results/'+self.renderer+'_step3_' + str(iter))
                if self.renderer == 'vertex':
                    mitsuba_img = self.pipeline.renderMitsuba(cameraVerts, vDiffTextures, vSpecTextures, vRoughTextures) #mitsuba
                    self.debugRender(mitsuba_img[..., 0:3],self.debugDir + '/results/ref') # save a ref of the image
                    lightingVertexRender = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo, lightingOnly=True)
                    albedoVertexRender = self.pipeline.renderVertexBased(cameraVerts, diffAlbedo, specAlbedo, albedoOnly=True)
                    self.debugIteration(rgba_img[..., 0:3], inputTensor,diff, albedoVertexRender, lightingVertexRender, self.debugDir + '/results/'+self.renderer+ str(iter)+'_detailled_step3') # custom made
                # also save obj
                cameraNormals = self.pipeline.morphableModel.computeNormals(cameraVerts) # only used of obj (might be too slow)
                for i in range(inputTensor.shape[0]):
                    saveObj(self.debugDir + '/mesh/' + self.renderer+'_step3_iter_' + str(iter)+'.obj',
                            'material' + str(iter) + '.mtl',
                            cameraVerts[i],
                            self.pipeline.faces32,
                            cameraNormals[i],
                            self.pipeline.morphableModel.uvMap,
                            self.debugDir + 'diffuseMap_' + str(self.getTextureIndex(i)) + '.png')
                   
        self.plotLoss(losses, 1, self.outputDir + 'checkpoints/stage3_loss.png')
        self.saveParameters(self.outputDir + 'checkpoints/stage3_output.pickle')

        self.vEnhancedDiffuse = vDiffTextures.detach().clone()
        self.vEnhancedSpecular = vSpecTextures.detach().clone()
        self.vEnhancedRoughness = vRoughTextures.detach().clone()

    def saveOutput(self, samples,  outputDir = None, prefix = ''):
        if outputDir is None:
            outputDir = self.outputDir
            mkdir_p(outputDir)

        print("saving to: '", outputDir, "'. hold on... ", file=sys.stderr, flush=True)
        outputDir += '/' #use join

        inputTensor = torch.pow(self.inputImage.tensor, self.inputImage.gamma)
        vDiffTextures = self.vEnhancedDiffuse
        vSpecTextures = self.vEnhancedSpecular
        vRoughTextures = self.vEnhancedRoughness
        vertices, diffAlbedo, specAlbedo = self.pipeline.morphableModel.computeShapeAlbedo(self.pipeline.vShapeCoeff, self.pipeline.vExpCoeff, self.pipeline.vAlbedoCoeff)
        cameraVerts = self.pipeline.camera.transformVertices(vertices, self.pipeline.vTranslation, self.pipeline.vRotation)
        cameraNormals = self.pipeline.morphableModel.computeNormals(cameraVerts)

        if vDiffTextures is None:
            vDiffTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(diffAlbedo)
            vSpecTextures = self.pipeline.morphableModel.generateTextureFromAlbedo(specAlbedo)
            vRoughTextures = self.pipeline.vRoughness

        self.pipeline.renderer.samples = samples
        images = self.pipeline.renderMitsuba(None, vDiffTextures, vSpecTextures, vRoughTextures)
        # clamp it !
        vSpecTextures.clamp(0,1)
        diffuseAlbedo = self.pipeline.renderMitsuba(diffuseTextures=vDiffTextures, renderAlbedo=True)
        specularAlbedo = self.pipeline.renderMitsuba(diffuseTextures=vSpecTextures, renderAlbedo=True)
        roughnessAlbedo = self.pipeline.renderMitsuba(diffuseTextures=vRoughTextures.repeat(1, 1, 1, 3), renderAlbedo=True)
        illum = self.pipeline.renderMitsuba(diffuseTextures=torch.ones_like(vDiffTextures), specularTextures=torch.zeros_like(vDiffTextures))

        for i in range(diffuseAlbedo.shape[0]):
            saveObj(outputDir + prefix + '/mesh' + str(i) + '.obj',
                    'material' + str(i) + '.mtl',
                    cameraVerts[i],
                    self.pipeline.faces32,
                    cameraNormals[i],
                    self.pipeline.morphableModel.uvMap,
                    prefix + 'diffuseMap_' + str(self.getTextureIndex(i)) + '.png')

            envMaps = self.pipeline.sh.toEnvMap(self.pipeline.vShCoeffs, self.config.smoothSh) #smooth
            ext = '.png'
            if self.config.saveExr:
                ext = '.exr'
            saveImage(envMaps[i], outputDir + '/envMap_' + str(i) + ext)

            saveImage(diffuseAlbedo[self.getTextureIndex(i)],  outputDir + prefix +  'diffuse_' + str(self.getTextureIndex(i)) + '.png')
            saveImage(specularAlbedo[self.getTextureIndex(i)], outputDir + prefix + 'specular_' + str(self.getTextureIndex(i)) + '.png')
            saveImage(roughnessAlbedo[self.getTextureIndex(i)], outputDir + prefix + 'roughness_' + str(self.getTextureIndex(i)) + '.png')
            saveImage(illum[i], outputDir + prefix + 'illumination_' + str(i) + '.png')
            saveImage(images[i], outputDir + prefix + 'finalReconstruction_' + str(i) + '.png')
            overlay = overlayImage(inputTensor[i], images[i])
            saveImage(overlay, outputDir + '/overlay_' + str(i) + '.png')

            renderAll = torch.cat([torch.cat([inputTensor[i], torch.ones_like(images[i])[..., 3:]], dim = -1),
                           torch.cat([overlay.to(self.device), torch.ones_like(images[i])[..., 3:]], dim = -1),
                           images[i],
                           illum[i],
                           diffuseAlbedo[self.getTextureIndex(i)],
                           specularAlbedo[self.getTextureIndex(i)],
                          roughnessAlbedo[self.getTextureIndex(i)]], dim=1)
            saveImage(renderAll, outputDir + '/render_' + str(i) + '.png')
            saveImage(vDiffTextures[self.getTextureIndex(i)], outputDir + prefix + 'diffuseMap_' + str(self.getTextureIndex(i)) + '.png')
            saveImage(vSpecTextures[self.getTextureIndex(i)], outputDir + prefix + 'specularMap_' + str(self.getTextureIndex(i)) + '.png')
            saveImage(vRoughTextures[self.getTextureIndex(i)].repeat(1, 1, 3), outputDir + prefix  + 'roughnessMap_' + str(self.getTextureIndex(i)) + '.png')

    def run(self, imagePathOrDir, sharedIdentity = False, checkpoint = None, doStep1 = True, doStep2 = True, doStep3 = True, renderer= 'mitsuba'):
        '''
        run optimization on given path (can be a directory that contains images with same resolution or a direct path to an image)
        :param imagePathOrDir: a path to a directory or image
        :param sharedIdentity: if True, the images in the directory belongs to the same subject so the shape identity and skin reflectance are shared across all images
        :param checkpoint: a path to a  checkpoint file (pickle)  to resume optim (check saveParameters and loadParameters)
        :param doStep1: if True do stage 1 optim (landmarks loss)
        :param doStep2: if True do stage 2 optim (photo loss on statistical prior)
        :param doStep3: if True do stage 3 optim ( refine albedos)
        :return:
        '''
        self.renderer = renderer
        self.setImage(imagePathOrDir, sharedIdentity)
        assert(self.framesNumber >= 1) #could not load any image from path

        if checkpoint is not None and checkpoint != '':
            print('resuming optimization from checkpoint: ',checkpoint, file=sys.stderr, flush=True)
            self.loadParameters(checkpoint)
            # hardcoded for now
            # Extract the directory and file name
            # checkpoint_dir, checkpoint_file = os.path.split(checkpoint)            
            # self.loadAlbedoParameters(os.path.join(checkpoint_dir, "stage2_output.pickle"))
        import time
        start = time.time()
        if doStep1:
            self.runStep1()
            if self.config.saveIntermediateStage:
                self.saveOutput(self.outputDir + '/outputStage1', prefix='stage1_')
        if doStep2:
            self.runStep2()
            if self.config.saveIntermediateStage:
                self.saveOutput(self.outputDir + '/outputStage2', prefix='stage2_')
        if doStep3:
            self.runStep3()
        end = time.time()
        message = "took {:.2f} minutes to optimize".format((end - start) / 60.)
        print(message, file=sys.stderr, flush=True)

        # Create a unique file name based on the current timestamp
        file_name = self.outputDir + "/run_time"+ renderer + ".txt"

        # Open the file and write the message
        with open(file_name, "w") as file:
            file.write(message)
        self.saveOutput(self.outputDir)
    
    def getMask(self, cameraVerts, diffuseAlbedo):
       """generate a vertexBased Image and only take the alpha part
       slower but cant find a mitsuba alternative that works 

        Returns:
            _type_: _description_
       """
       initialMask = self.pipeline.renderVertexBased(cameraVerts, diffuseAlbedo)[..., 3:]
       # the mask is fileld with black holes from gaps between vertex
       # Assuming mask is your mask image with random black points
       # Convert the mask to uint8 format
       mask = (initialMask[0].detach().cpu().numpy() * 255).astype(np.uint8)
       # Define a kernel. The size of the kernel affects how many pixels around the black point will be considered
       kernel = np.ones((5,5),np.uint8)
       # Perform the closing operation
       mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
       mask = torch.from_numpy(mask / 255.0).unsqueeze(0).unsqueeze(-1).to(self.device).to(torch.float32)
    #    self.debugTensor(mask)
       return mask
   
if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=False, default='./input/s1.png', help="path to a directory or image to reconstruct (images in same directory should have the same resolution")

    parser.add_argument("--sharedIdentity", dest='sharedIdentity', action='store_true', help='in case input directory contains multiple images, this flag tells the optimizations that all images are for the same person ( that means the identity shape and skin reflectance is common for all images), if this flag is false, that each image belong to a different subject', required=False)
    #parser.add_argument("--no-sharedIdentity", dest='sharedIdentity', action='store_false', help='in case input directory contains multiple images, this flag tells the optimizations that all images are for the same person ( that means the identity shape and skin reflectance is common for all images), if this flag is false, that each image belong to a different subject', required=False)

    parser.add_argument("--output", required=False, default='./output/', help="path to the output directory where optimization results are saved in")
    parser.add_argument("--config", required=False, default='./optimConfig.ini', help="path to the configuration file (used to configure the optimization)")

    parser.add_argument("--checkpoint", required=False, default='', help="path to a checkpoint pickle file used to resume optimization")
    parser.add_argument("--skipStage1", dest='skipStage1', action='store_true', help='if true, the first (coarse) stage is skipped (stage1). useful if u want to resume optimization from a checkpoint', required=False)
    parser.add_argument("--skipStage2", dest='skipStage2', action='store_true', help='if true, the second stage is skipped (stage2).  useful if u want to resume optimization from a checkpoint', required=False)
    parser.add_argument("--skipStage3", dest='skipStage3', action='store_true', help='if true, the third stage is skipped (stage3).  useful if u want to resume optimization from a checkpoint', required=False)
    params = parser.parse_args()

    inputDir = params.input
    sharedIdentity = params.sharedIdentity
    outputDir = params.output + '/' + os.path.basename(inputDir.strip('/'))

    configFile = params.config
    checkpoint = params.checkpoint
    doStep1 = not params.skipStage1
    doStep2 = not params.skipStage2
    doStep3 = not params.skipStage3

    config = Config()
    config.fillFromDicFile(configFile)
    if config.device == 'cuda' and torch.cuda.is_available() == False:
        print('[WARN] no cuda enabled device found. switching to cpu... ')
        config.device = 'cpu'

    #check if mediapipe is available

    if config.lamdmarksDetectorType == 'mediapipe':
        try:
            from  landmarksmediapipe import LandmarksDetectorMediapipe
        except:
            print('[WARN] Mediapipe for landmarks detection not availble. falling back to FAN landmarks detector. You may want to try Mediapipe because it is much accurate than FAN (pip install mediapipe)')
            config.lamdmarksDetectorType = 'fan'

    optimizer = Optimizer(outputDir, config)
    optimizer.run(inputDir,
                  sharedIdentity= sharedIdentity,
                  checkpoint= checkpoint,
                  doStep1= doStep1,
                  doStep2 = doStep2,
                  doStep3= doStep3)